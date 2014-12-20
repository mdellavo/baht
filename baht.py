#!/usr/bin/env python
from datetime import datetime
import logging

import sys
import argparse

import irc.client
import irc.logging
import re
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

    nick = event.source.split('!')[0]
    user = session.query(User).filter_by(nick=nick).first()
    if not user:
        user = User(nick=nick)
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
                    posted_by=nick,
                )
                session.add(u)
                score = 1
            else:
                user.reposts += 1
                score = -1

            u.last_seen = datetime.utcnow()

            connection.privmsg(bot.args.channel, "{}: {}".format(nick, score))
            dirty = True

    if dirty:
        session.commit()

class Bot(irc.client.SimpleIRCClient):
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

