import os
from fabric.context_managers import prefix
from fabric.contrib.project import rsync_project
from fabric.decorators import task
from fabric.operations import run


def virtualenv(name):
    return "source " + os.path.join("/", "home", "marc", ".virtualenvs", name, "bin", "activate")

@task
def upload():
    ignore = (".git", "baht.db", ".gitignore", ".idea", "*.pyc", "fabfile.*")
    rsync_project("baht", ".", ignore)

@task
def init_env():
    with prefix(virtualenv("baht")):
        run("pip install -r baht/requirements.txt")

@task
def spawn(host, nick, channel):
    run("tmux new-window -n \"baht:{}:{}\" '{} && python ~/baht/baht.py {} {} \"{}\"'".format(virtualenv("baht"), host, nick, host, nick, channel))

@task
def deploy(host, nick, channel):
    upload()
    init_env()
    spawn(host, nick, channel)