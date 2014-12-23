#!/usr/bin/env python

from datetime import datetime
import logging
import random
import ssl
import sys
import argparse
import re
import humanize

from irc.client import SimpleIRCClient, NickMask
import irc.logging
import itertools
import requests
from sqlalchemy import Column, String, Integer, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


log = logging.getLogger(__name__)

Base = declarative_base()

URL_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

Session = sessionmaker()

ANNOY = False

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
    reddit_posts = Column(Integer, default=0)


def fetch_reddit():
    headers = {
        'User-Agent': 'baht 0'
    }

    resp = requests.get('https://www.reddit.com/.json?limit=100', headers=headers)
    if resp.status_code != 200:
        return {}

    return {child['data']['url']: child['data'] for child in resp.json()['data']['children']}


def scrape_urls(bot, connection, event):

    session = Session()

    nickmask = NickMask(event.source)
    user = session.query(User).filter_by(nick=nickmask.nick).first()
    if not user:
        user = User(nick=nickmask.nick)
        session.add(user)
        session.commit()

    urls = [url for arg in event.arguments for url in URL_PATTERN.findall(arg)]

    reddit_urls = fetch_reddit() if urls else {}

    for url in urls:

        u = session.query(Url).filter_by(url=url).first()
        if not u:
            u = Url(
                url=url,
                posted_by=nickmask.nick,
            )
            user.posts += 1
            session.add(u)

        u.last_seen = datetime.utcnow()
        print url
        if url in reddit_urls.keys():
            user.reddit_posts += 1
            if ANNOY:
                say_to(bot, connection, event, "anything else from reddit bruh?")
        elif u.posted_by != nickmask.nick:
            user.reposts += 1
            ago = datetime.utcnow() - u.first_seen
            say_to(bot, connection, event, "repost shitbag, {} posted this {}", u.posted_by, humanize.naturaltime(ago))

    session.commit()

is_regex = lambda s: s.startswith('/') and s.endswith('/')

def take(n, iterable):
    "Return first n items of the iterable as a list"
    return list(itertools.islice(iterable, n))


class InvalidCommand(Exception):
    def __init__(self, msg='say what?', *args, **kwargs):
        super(InvalidCommand, self).__init__(msg, *args, **kwargs)

class Commands(object):

    def score(self, bot, connection, event, args):
        """return a user's score"""

        if len(args) == 0:
            raise InvalidCommand

        user = Session().query(User).filter_by(nick=args[0]).first()
        if not user:
            raise InvalidCommand("eat shit")

        percent = lambda a, b: int(round(float(a) / float(b) * 100))
        say(bot, connection,
            "{0: >8} : posts: {1} / reposts: {2} ({3}%) / reddit: {4} ({5}%)",
            user.nick, user.posts, user.reposts, percent(user.reposts, user.posts), user.reddit_posts, percent(user.reddit_posts, user.posts))

    def help(self, bot, connection, event, args):
        """say what?"""

        command_names = [attr for attr in dir(self) if attr[0] != '_']
        commands = {command_name: getattr(self, command_name) for command_name in command_names}

        for command_name in sorted(command_names):
            say(bot, connection, "{0: >8} : {1}", command_name, commands[command_name].__doc__)

    def url(self, bot, connection, event, args):
        """find urls by nick or /regex/"""

        if len(args) == 0:
            raise InvalidCommand()

        if is_regex(args[0]):
            pattern_s = args[0][1:-1]

            try:
                query = Session().query(Url).sort_by(Url.last_seen.desc())
                pattern = re.compile(pattern_s)
                matches = take(5, (url for url in query if pattern.search(url.url)))
            except:
                raise InvalidCommand("say what? " + args[0])
        else:
            matches = Session().query(Url).filter_by(posted_by=args[0]).limit(5).all()

        if matches:
            say(bot, connection, " | ".join([url.url for url in matches]))
        else:
            say_to(bot, connection, event, "eat shit")

    def __call__(self, bot, connection, event):
        args = event.arguments[0].split()
        command_name = args[0][1:]
        command = getattr(self, command_name, None)
        if command:
            try:
                command(bot, connection, event, args[1:])
            except InvalidCommand, e:
                say_to(bot, connection, event, e.message)
        else:
            say_to(bot, connection, event, "wtf {}", command_name)


def parse_command(bot, connection, event):
    command = Commands()
    command(bot, connection, event)


is_command = lambda event: event.arguments[0].startswith('!')

def say(bot, connection, fmt, *args, **kwargs):
    connection.privmsg(bot.args.channel, fmt.format(*args, **kwargs))


def say_to(bot, connection, event, fmt, *args, **kwargs):
    nickmask = NickMask(event.source)
    connection.privmsg(bot.args.channel, nickmask.nick + ": " + fmt.format(*args, **kwargs))

GREETINGS = ['sup', 'ahoy', 'yo', 'high']

class Bot(SimpleIRCClient):
    def __init__(self, args):
        super(Bot, self).__init__()
        self.args = args

    def on_welcome(self, connection, event):
        log.info('connected to %s, joining %s...', self.args.server, self.args.channel)
        connection.join(self.args.channel)

    def on_join(self, connection, event):

        nickmask = NickMask(event.source)

        greeting = random.choice(GREETINGS)

        if ANNOY:
            if nickmask.nick != self.args.nickname:
                say_to(self, connection, event, greeting)
            else:
                say(self, connection, greeting)

    def on_disconnect(self, connection, event):
        raise SystemExit()

    def on_pubmsg(self, connection, event):

        if is_command(event):
            parse_command(self, connection, event)
        else:
            scrape_urls(self, connection, event)


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('server')
    parser.add_argument('nickname')
    parser.add_argument('channel')
    parser.add_argument('-p', '--port', default=9999, type=int)
    irc.logging.add_arguments(parser)
    return parser.parse_args()


def main():
    args = get_args()

    engine = create_engine('sqlite:///baht.db', echo=True)
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)

    irc.logging.setup(args)

    bot = Bot(args)

    try:
        ssl_factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
        bot.connect(
            args.server,
            args.port,
            args.nickname,
            connect_factory=ssl_factory
        )
    except irc.client.ServerConnectionError as x:
        print(x)
        sys.exit(1)

    try:
        bot.start()
    except KeyboardInterrupt:
        bot.reactor.disconnect_all("eat shit")


if __name__ == '__main__':
    main()

