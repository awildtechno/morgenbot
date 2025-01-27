import os
import re
import json
import datetime
import time
import random
from collections import namedtuple

from slacker import Slacker

from flask import Flask, request

app = Flask(__name__)

curdir = os.path.dirname(os.path.abspath(__file__))
os.chdir(curdir)

slack = Slacker(os.getenv("TOKEN"))
username = os.getenv("USERNAME", "StandupBot")
icon_emoji = os.getenv("ICON_EMOJI", ":coffee:")
channel = os.getenv("CHANNEL", "#standup")

ignore_users = os.getenv("IGNORE_USERS", "")
ignore_users = ignore_users.split(",")
ignore_users = [usr.strip() for usr in ignore_users]

init_greeting = os.getenv("INIT_GREETING", "Good morning!")
start_message = os.getenv(
    "START_MESSAGE",
    "What did you work on yesterday? What are you working on today? What, if any, are your blockers?",
)

giphy = os.getenv("GIPHY") == "true"

Command = namedtuple("Command", "name,help,callable")

commands = {
    "standup": Command("standup", "Type !standup to initiate a new standup"),
    "start": Command("start", "Type !start to get started with standup once everyone is ready"),
    "cancel": Command("cancel", "Type !cancel if you'd like to stop the standup entirely."),
    "next": Command("next", "Type !next to call on the next person when you're done standing up"),
    "skip": Command("skip", "Type !skip to skip someone who isn't standing up that day"),
    "later": Command("later", "Type !later to move someone who isn't ready yet to the end of the list"),
    "table": Command("table", "left", "Type !left to find out who is left in the standup"),
    "ignore": Command("ignore", "Type !ignore <username> to temporarily skip a user during standup for a while"),
    "unignore": Command("unignore", "Type !unignore <username> to add an ignored user back, starting with the next standup"),
    "ignoring": Command("ignoring", "Type !ignoring to find out who we're skipping over for standups"),
    "ready": Command("ready", "Type !ready to skip ahead in the queue and give your standup immediately"),
    "help": Command("help", "Show this message")
}

users = set()
topics = set()
time = []
in_progress = False
current_user = ""
absent_users = set()


def say(text, attachments=[]):
    slack.chat.say(
        channel=channel,
        text=text,
        username=username,
        parse="full",
        link_names=1,
        attachments=attachments,
        icon_emoji=icon_emoji,
    )


def get_user(id):
    user = slack.users.info(id).body
    return user["user"]["name"]


def get_channel(id):
    channel = slack.channels.info(id).body
    return channel["channel"]["name"]


def init():
    global users
    global topics
    global time
    global in_progress

    if len(users) != 0:
        say("Looks like we have a standup already in process.")
        return
    users = standup_users()
    topics = []
    time = []
    in_progress = True
    say(
        "%s, @channel! Please type !start when you are ready to stand up."
        % init_greeting
    )


def start():
    global time

    if len(time) != 0:
        say("But we've already started!")
        return
    time.append(datetime.datetime.now())
    say(
        "Let's get started! %s\nWhen you're done, please type !next" % start_message
    )
    next()


def cancel():
    tabled()
    say("Standup is cancelled. Bye!")
    reset()


def done():
    global time

    time.append(datetime.datetime.now())
    standup_time()
    tabled()
    say("Bye!")
    reset()


def reset():
    global users
    global topics
    global time
    global in_progress
    global current_user

    del users[:]
    del topics[:]
    del time[:]
    in_progress = False
    current_user = ""


def standup_users():
    global ignore_users
    global absent_users

    channel_id = ""
    channel_name = channel.replace(
        "#", ""
    )  # for some reason we skip the # in this API call
    all_channels = slack.channels.list(1)  # 1 means we skip any archived rooms
    for one_channel in all_channels.body["channels"]:
        if one_channel["name"] == channel_name:
            channel_id = one_channel["id"]

    standup_room = slack.channels.info(channel_id).body["channel"]
    standup_users = standup_room["members"]
    active_users = []

    for user_id in standup_users:
        user_name = slack.users.info(user_id).body["user"]["name"]
        is_deleted = slack.users.info(user_id).body["user"]["deleted"]
        if (
            not is_deleted
            and user_name not in ignore_users
            and user_name not in absent_users
        ):
            active_users.append(user_name)

    # don't forget to shuffle so we don't go in the same order every day!
    random.shuffle(active_users)

    return active_users


def next_user():
    global users
    global current_user

    if len(users) == 0:
        done()
    else:
        current_user = users.pop()
        say("@%s, you're up" % current_user)


def standup_time():
    if len(time) != 2:
        return
    seconds = (time[1] - time[0]).total_seconds()
    minutes = seconds / 60
    say("That's everyone! Standup took us %d minutes." % minutes)


def left():
    if len(users) == 0:
        say("That's everyone!")
    else:
        say("Here's who's left: @" + ", @".join(users))


def ignore(user):
    global ignore_users
    global absent_users
    active_users = standup_users()

    if user == "":
        say("Who should I ignore?")
        return

    user = user[1:]
    if (
        user not in active_users
        and user not in ignore_users
        and user not in absent_users
    ):
        say("I don't recognize that user.")
    elif user in ignore_users or user in absent_users:
        say("I'm already ignoring that user.")
    elif user in active_users:
        absent_users.append(user)
        say(
            "I won't call on @%s again until I am told to using !heed <username>."
            % user
        )


def heed(user):
    global ignore_users
    global absent_users
    active_users = standup_users()

    if user == "":
        say("Who should I heed?")
        return

    user = user[1:]
    if (
        user not in active_users
        and user not in ignore_users
        and user not in absent_users
    ):
        say("I don't recognize that user.")
    elif user in ignore_users:
        say(
            "We never call on that user. Try asking my admin to heed that username."
        )
    elif user in active_users:
        say("I'm not ignoring that user.")
    elif user in absent_users:
        absent_users.remove(user)
        say("I'll start calling on @%s again at the next standup." % user)


def ignoring():
    global ignore_users
    global absent_users

    if len(ignore_users) == 0 and len(absent_users) == 0:
        say("We're not ignoring anyone.")
        return

    if len(ignore_users) != 0:
        say("Here's who we never call on: " + ignore_users)
    if len(absent_users) != 0:
        say("Here's who we're ignoring for now: " + ", ".join(absent_users))


def skip():
    say("Skipping @%s." % current_user)
    next()


def later():
    say("We'll call on @%s later." % current_user)
    users.append(current_user)
    next()


def table(topic_user, topic):
    global topics

    channels = re.findall(r"<#(.*?)>", topic)
    users = re.findall(r"<@(.*?)>", topic)

    for channel in channels:
        channel_name = get_channel(channel)
        topic = topic.replace("<#%s>" % channel, "#%s" % channel_name)

    for user in users:
        user_name = get_user(user)
        topic = topic.replace("<@%s>" % user, "@%s" % user_name)

    say("@%s: Tabled." % topic_user)
    topics.append(str(topic))


def tabled():
    if len(topics) == 0:
        return
    say("Tabled topics:")
    for topic in topics:
        say("-%s" % topic)


def giphy(text):
    url = (
        "http://api.giphy.com/v1/gifs/search?q=%s&api_key=dc6zaTOxFJmzC&limit=1"
        % urllib2.quote(text.encode("utf8"))
    )
    response = urllib2.urlopen(url)
    data = json.loads(response.read())

    if len(data["data"]) == 0:
        say('Not sure what "%s" is.' % text)
    else:
        attachments = [
            {
                "fallback": text,
                "title": text,
                "title_link": data["data"][0]["url"],
                "image_url": data["data"][0]["images"]["fixed_height"]["url"],
            }
        ]

        say('Not sure what "%s" is.' % text, json.dumps(attachments))


def ready(msguser):
    global ignore_users
    global absent_users
    global current_user
    global users
    active_users = standup_users()

    if msguser == "":
        say("Your username is blank. Are you a ghost?")
        return

    if (
        msguser not in active_users
        and msguser not in ignore_users
        and msguser not in absent_users
    ):
        say("I don't recognize you. How did you get in here?")
    elif msguser in ignore_users:
        say("I'm ignoring you. Try asking my admin to heed you.")
    elif msguser in absent_users:
        say("I'll come back to you, @%s" % current_user)
        users.append(current_user)
        current_user = msguser
        absent_users.remove(msguser)
        say("Welcome back, @%s. We will call on you from now on." % msguser)
    elif msguser in users:
        say("I'll come back to you, @%s" % current_user)
        users.append(current_user)
        current_user = msguser
        users.remove(msguser)
        say("Alright @%s, go ahead" % msguser)
    elif msguser == current_user:
        say("It's already your turn. Go ahead.")
    else:
        say("You already went during this standup")

def help(topic=""):
    topics = ", ".join([f"!{topic}" for topic in commands])
    if topic == "":
        say(f"My commands are {topics}. Type !help <topic> for more info on that command.")
        return

    if topic.startswith("!"):
        topic = topic[1:]
    if topic in commands:
        say(commands[topic])
    else:
        say(f"I don't know {topic}. Please choose from {topics}")

@app.route("/", methods=["POST"])
def main():
    # ignore message we sent
    msguser = request.form.get("user_name", "").strip()
    if msguser == username or msguser.lower() == "slackbot":
        return

    text = request.form.get("text", "")

    match = re.findall(r"^!(\S+)", text)
    if not match:
        return

    command = match[0]
    args = text[text.find("!%s" % command) + len(command) + 1 :]
    command = command.lower()

    if command not in commands:
        if giphy:
            giphy("%s %s" % (command, args))
        else:
            say('Not sure what "%s" is.' % command)
        return json.dumps({})
    elif (
        not in_progress
        and command != "standup"
        and command != "help"
        and command != "ignore"
        and command != "heed"
        and command != "ignoring"
    ):
        say("Looks like standup hasn't started yet. Type !standup.")
        return json.dumps({})

    if command == "standup":
        init()
    elif command == "start":
        start()
    elif command == "cancel":
        cancel()
    elif command == "next":
        next()
    elif command == "skip":
        skip()
    elif command == "later":
        later()
    elif command == "table":
        table(msguser, args)
    elif command == "left":
        left()
    elif command == "ignore":
        ignore(args)
    elif command == "heed":
        heed(args)
    elif command == "ignoring":
        ignoring()
    elif command == "help":
        help(args)
    elif command == "ready":
        ready(msguser)

    return json.dumps({})


if __name__ == "__main__":
    app.run(debug=True)
