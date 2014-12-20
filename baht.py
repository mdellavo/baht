#!/usr/bin/env python

from datetime import datetime
import logging
import sys
import argparse
import re
import humanize

from irc.client import SimpleIRCClient, NickMask
import irc.logging
import itertools
from sqlalchemy import Column, String, Integer, DateTime, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


log = logging.getLogger(__name__)

Base = declarative_base()

URL_PATTERN = re.compile(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

Session = sessionmaker()


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


def scrape_urls(bot, connection, event):
    dirty = False

    session = Session()

    nickmask = NickMask(event.source)
    user = session.query(User).filter_by(nick=nickmask.nick).first()
    if not user:
        user = User(nick=nickmask.nick)
        session.add(user)
        dirty = True

    for arg in event.arguments:
        for url in URL_PATTERN.findall(arg):
            log.debug('matched: %s', url)

            user.posts += 1

            u = session.query(Url).filter_by(url=url).first()
            if not u:
                u = Url(
                    url=url,
                    posted_by=nickmask.nick,
                )
                session.add(u)
                score = 1
            else:
                user.reposts += 1
                ago = datetime.utcnow() - u.first_seen
                say_to(bot, connection, event, "repost shitbag, {} posted this {}", u.posted_by, humanize.naturaltime(ago))

            u.last_seen = datetime.utcnow()

            dirty = True

    if dirty:
        session.commit()

is_regex = lambda s: s.startswith('/') and s.endswith('/')

def take(n, iterable):
    "Return first n items of the iterable as a list"
    return list(itertools.islice(iterable, n))


class InvalidCommand(Exception):
    pass

class Commands(object):
    def url(self, bot, connection, event, args):

        if len(args) == 0:
            raise InvalidCommand('say what?')

        if is_regex(args[0]):
            pattern_s = args[0][1:-1]

            try:
                pattern = re.compile(pattern_s)
                matches = take(5, (url for url in Session().query(Url) if pattern.search(url.url)))
            except:
                raise InvalidCommand("say what? " + args[0])
        else:
            matches = Session().query(Url).filter_by(posted_by=args[0]).limit(5).all()

        if matches:
            say_to(bot, connection, event, " | ".join([url.url for url in matches]))
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


def say_to(bot, connection, event, fmt, *args, **kwargs):
    nickmask = NickMask(event.source)
    connection.privmsg(bot.args.channel, nickmask.nick + ": " + fmt.format(*args, **kwargs))


class Bot(SimpleIRCClient):
    def __init__(self, args):
        super(Bot, self).__init__()
        self.args = args

    def on_welcome(self, connection, event):
        log.info('connected to %s, joining %s...', self.args.server, self.args.channel)
        connection.join(self.args.channel)

    def on_join(self, connection, event):
        connection.privmsg(self.args.channel, "hello")

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
    parser.add_argument('-p', '--port', default=6667, type=int)
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
        bot.connect(args.server, args.port, args.nickname)
    except irc.client.ServerConnectionError as x:
        print(x)
        sys.exit(1)

    bot.start()

if __name__ == '__main__':
    main()

