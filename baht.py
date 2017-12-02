#!/usr/bin/env python

import os
import re
import ssl
import sys
import random
import logging
import argparse
import itertools
from datetime import datetime

import emoji
import humanize
import requests

from irc.bot import SingleServerIRCBot, ExponentialBackoff
from irc.connection import Factory
from irc.client import ServerConnection

from sqlalchemy import Column, String, Integer, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from jaraco.stream import buffer
ServerConnection.buffer_class = buffer.LenientDecodingLineBuffer

log = logging.getLogger('baht')

RECONNECT_TIMEOUT = 5
URL_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
USER_AGENT = "baht 1.0"

Base = declarative_base()
Session = sessionmaker()

REDDIT_HISTORY = []
REDDIT_HISTORY_MAX = 1000

GIFY_API_KEY = os.environ.get("GIFY_API_KEY")


class Url(Base):
    __tablename__ = 'urls'

    id = Column(Integer, primary_key=True)
    url = Column(String, nullable=False, unique=True)
    posted_by = Column(String, nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime)


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    nick = Column(String, nullable=False, unique=True)

    posts = Column(Integer, default=0)
    reposts = Column(Integer, default=0)


def scrape_urls(bot, event):
    session = Session()

    user = session.query(User).filter_by(nick=event.source.nick).first()
    if not user:
        user = User(nick=event.source.nick)
        session.add(user)

    urls = [url for arg in event.arguments for url in URL_PATTERN.findall(arg)]

    for url in urls:
        u = session.query(Url).filter_by(url=url).first()
        if not u:
            u = Url(
                url=url,
                posted_by=event.source.nick,
            )
            user.posts += 1
            session.add(u)

        u.last_seen = datetime.utcnow()
        if u.posted_by != event.source.nick:
            user.reposts += 1
            ago = datetime.utcnow() - u.first_seen
            bot.say_to(event, "repost, {} posted this {}", u.posted_by, humanize.naturaltime(ago))

    session.commit()


def is_regex(s):
    return s.startswith('/') and s.endswith('/')


def take(n, iterable):
    """Return first n items of the iterable as a list"""
    return list(itertools.islice(iterable, n))


class Commands(object):

    def emoji(self, bot, event, args):
        if not args:
            return

        rv = emoji.emojize(":" + args[0] + ":", use_aliases=True)
        if rv:
            bot.say(rv)

    def score(self, bot, event, args):
        """return a user's score"""

        if not args:
            return

        user = Session().query(User).filter_by(nick=args[0]).first()
        if not user:
            return

        percent = lambda a, b: int(round(float(a) / float(a + b) * 100)) if (a + b) > 0 else 0
        bot.say(u"{0: >8} : posts: {1} / reposts: {2} ({3}%)",
                user.nick, user.posts, user.reposts, percent(user.reposts, user.posts))

    def help(self, bot, event, args):
        """say what?"""
        command_names = [attr for attr in dir(self) if attr[0] != '_']
        bot.say(u" | ".join(sorted(command_names)))

    def url(self, bot, event, args):
        """find urls by nick or /regex/"""

        if not args:
            return

        if is_regex(args[0]):
            pattern_s = args[0][1:-1]
            query = Session().query(Url).order_by(Url.last_seen.desc())
            pattern = re.compile(pattern_s)
            matches = take(5, (url for url in query if pattern.search(url.url)))
        else:
            matches = Session().query(Url).filter_by(posted_by=args[0]).order_by(Url.last_seen.desc()).limit(5).all()

        if matches:
            bot.say(u" | ".join([url.url for url in matches]))

    def reddit(self, bot, event, args):
        if not args:
            return

        about_url = "https://reddit.com/r/{}.json".format(args[0])
        response = requests.get(about_url, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            log.error("invalid response: %s", response)
            return

        json = response.json()

        def is_image(child):
            return child["kind"] == "t3" and child["data"].get("thumbnail", "").startswith("http")

        session = Session()
        items = [child["data"] for child in json["data"]["children"] if is_image(child)]
        found = None
        for i in range(len(items)):
            candidate = random.choice(items)
            url = candidate["url"]

            exists = session.query(Url).filter_by(url=url).first()
            if not exists and url not in REDDIT_HISTORY:
                found = candidate
                break

            items.remove(candidate)
        if not found:
            return

        title = found["title"]
        url = found["url"]
        bot.say(u"{} - {}", url, title)

        REDDIT_HISTORY.append(url)
        while len(REDDIT_HISTORY) > REDDIT_HISTORY_MAX:
            REDDIT_HISTORY.pop(0)

    def gif(self, bot, event, args):
        if not args:
            return

        if not GIFY_API_KEY:
            raise ValueError("GIFY_API_KEY is not set")

        response = requests.get("http://api.giphy.com/v1/gifs/random", params={"api_key": GIFY_API_KEY, "tag": args[0]})
        if response.status_code != 200:
            log.error("invalid response: %s", response)
            return

        json = response.json()
        image_url = json["data"]["image_url"]
        bot.say(image_url)

    def __call__(self, bot, event):
        args = event.arguments[0].split()
        command_name = args[0][1:]
        command = getattr(self, command_name, None)
        if command:
            try:
                command(bot, event, args[1:])
            except Exception as e:
                log.exception("error running command: %s", str(e))


def parse_command(bot, event):
    command = Commands()
    command(bot, event)


def is_command(event):
    return event.arguments[0].startswith('?')


class Bot(SingleServerIRCBot):
    def __init__(self, server_address, name, channel, ignore=None):
        super(Bot, self).__init__([server_address], name, name,
                                  recon=ExponentialBackoff(min_interval=RECONNECT_TIMEOUT,
                                                           max_interval=2 * RECONNECT_TIMEOUT),
                                  connect_factory=Factory(wrapper=ssl.wrap_socket))
        self.name = name
        self.channel = channel
        self.ignore = ignore or []

    @property
    def server_host(self):
        return self.server_list[0].host

    @property
    def server_port(self):
        return self.server_list[0].port

    def say(self, fmt, *args, **kwargs):
        self.connection.privmsg(self.channel, unicode(fmt).format(*args, **kwargs))

    def say_to(self, event, fmt, *args, **kwargs):
        self.say(event.source.nick + u": " + fmt, *args, **kwargs)

    def on_welcome(self, connection, event):
        log.info('connected to %s, joining %s...', self.server_host, self.channel)
        connection.join(self.channel)

    def on_pubmsg(self, connection, event):
        if event.source.nick in self.ignore:
            return

        if is_command(event):
            parse_command(self, event)
        elif event.source.nick != self.name:
            scrape_urls(self, event)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('server')
    parser.add_argument('name')
    parser.add_argument('channel')
    parser.add_argument('-p', '--port', default=9999, type=int)
    parser.add_argument('-i', '--ignore', action='append')
    return parser.parse_args()


def main():
    args = get_args()

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('[%(asctime)s] (%(levelname)s) %(name)s: %(message)s')
    ch.setFormatter(formatter)
    root.addHandler(ch)

    engine = create_engine('sqlite:///baht.db')
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)

    server_address = (args.server, args.port)
    name = args.name
    channel = args.channel
    if channel[0] != '#':
        channel = "#" + channel

    bot = Bot(server_address, name, channel, ignore=args.ignore)

    try:
        bot.start()
    except KeyboardInterrupt:
        bot.die()


if __name__ == '__main__':
    main()
