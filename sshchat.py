#!/usr/bin/env python3
import logging
import socket
import threading
import random
import hashlib
import argparse
import pickle
import os
from datetime import datetime

import paramiko

COLORS = [
    "\u001b[31m",
    "\u001b[32m",
    "\u001b[33m",
    "\u001b[34m",
    "\u001b[35m",
    "\u001b[36m",
    "\u001b[37m",
    ]

COLOR_RESET = "\u001b[0m"

chans = []

# DEFAULT CONFIG

USER_CFG_PATH = "usercfg.data"
RSA_PATH = "rsa.private"
BIND_IP = ""
PORT = 2222
SERVER_NAME = "PROUTROOM"
VERBOSE = False

# CFG END

CHATHELPMSG = ("\r\n[HELP]\r\n"
               "/help to call this help\r\n/"
               "exit to exit\r\n/"
               "msg [username] [msg] to privatly message with a specified user\r\n"
               "/status to view a quick status"
               "\r\n/passwd <new password> to set your password\r\n"
              )

def usage():
    return (
           "Py. SSHChat HELP\r\n\r\n"
           "INFO:\r\n"
           "\tSSHChat allows for hosting chatrooms which are accessible over SSH.\r\n"
           "\tIt is written completely in Python 3.\r\n"
           )

class ChatRoomServ(paramiko.ServerInterface):
    def __init__(self):
        self.event = threading.Event()

    def check_channel_request(self, kind, chanid):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        # if user's first login, register with entered password (stored as sha256)
        if not username in USER_CFG.keys():
            USER_CFG[username] = [hashlib.sha256(password.encode()).digest(), random.choice(COLORS)]

            # store data
            with open(USER_CFG_PATH, "wb") as picklefile:
                pickle.dump(USER_CFG, picklefile)

            return paramiko.AUTH_SUCCESSFUL

        if USER_CFG[username][0] == hashlib.sha256(password.encode()).digest():
            return paramiko.AUTH_SUCCESSFUL

        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True

    def check_channel_pty_request(self, *_): # yeah i don t care, just do it
        return True

class UserClass:
    msg = ""
    username = "???"
    usernamecolor = username

def build_status(userchan):
    return (f"\r\n[CHATROOM STATUS]\r\n"
            f"Server name: {SERVER_NAME}\r\n"
            f"Currently {len(chans)} user(s) online.\r\n"
            f"Your username: [{userchan.usersc.usernamecolor}]\r\n"
           )

def send_global(msg="", context="MESSAGE", usercolor="???", target=False):
    for chan in chans.copy():
        try:
            # check if user is targeted
            if target and chan.usersc.username not in target:
                continue

            # reset cursor, and send time string
            chan.send("\033[u")
            chan.send(datetime.now().strftime("(%H:%M) "))

            # send "private" string
            if target:
                chan.send(f"*private (involves {', '.join(target)})* ")

            if context=="MESSAGE":
                chan.send(f"[{usercolor}] {msg}\r\n")
            if context=="JOIN":
                chan.send(f"{{LOG}} {usercolor} has joined!\r\n")
            if context=="EXIT":
                chan.send(f"{{LOG}} {usercolor} has exited!\r\n")
            if context=="PLAIN":
                chan.send(msg)

            # store new cursor position, and then set it back to the top line
            chan.send(f"\033[s\033[0;0f\033[K{chan.usersc.msg}")
        except Exception as ex:
            logger.log(logging.INFO, ex)
            close_channel(chan)

def handle_user_input(chan):
    try:
        while True:
            chan.usersc.msg = ""
            while not chan.usersc.msg.endswith("\r"):
                transport = chan.recv(1024)

                # set cursor to 0, 0 and clear line
                chan.send("\033[0;0f\033[K")
                if transport == b"\x7f" and len(chan.usersc.msg):
                    chan.usersc.msg = chan.usersc.msg[:-1]
                elif transport == b"\x04":
                    # interpret ctrl-d (EOF) as exit
                    chan.usersc.msg = "/exit"
                    break
                else:
                    chan.usersc.msg += transport.decode("utf-8")

                # send whole text, rotated for max 60 characters
                chan.send(chan.usersc.msg[-60:])

            chan.send("\033[0;0f\033[K")
            msg = chan.usersc.msg.strip()
            chan.usersc.msg = ""

            # USER / COMMANDS
            if msg.startswith("/exit"):
                break

            if msg.startswith("/msg") and msg.split()>3:
                target = msg.split()[1]
                tmsg   = " ".join(msg.split()[2:])
                send_global(usercolor=chan.usersc.usernamecolor,
                            target=[target, chan.usersc.username], msg=tmsg)

            elif msg.startswith("/status"):
                send_global(msg=build_status(chan), target=[chan.usersc.username], context="PLAIN")

            elif msg.startswith("/passwd"):
                new_passwd = "" if len(msg.split())<2 else " ".join(msg.split()[1:])
                USER_CFG[chan.usersc.username][0] = hashlib.sha256(new_passwd.encode()).digest()

                 # store data
                with open(USER_CFG_PATH, "wb") as picklefile:
                    pickle.dump(USER_CFG, picklefile)
                send_global(msg="\r\n[PASSWD]\r\nYour password has been set.\r\n",
                            target=[chan.usersc.username], context="PLAIN")

            elif msg.startswith("/"):
                send_global(msg=CHATHELPMSG, target=[chan.usersc.username], context="PLAIN")

            elif msg:
                send_global(msg=msg, usercolor=chan.usersc.usernamecolor)
    except Exception as ex:
        logger.log(logging.INFO, ex)
    close_channel(chan)

def close_channel(chan):
    # send EXIT message to everyone, excluding exiter
    try:
        chans.remove(chan)
        send_global(context="EXIT", usercolor=chan.usersc.usernamecolor)
        logger.log(logging.INFO, f"{chan.usersc.username} has left")

        # clear, and set cursor to 0,0
        chan.send("\033[2J\033[0;0f")
    except Exception as ex:
        logger.log(logging.INFO, f"Error during closing of channel. ({ex})")
    chan.close()


def init_user(ca_pair):
    client, addr = ca_pair
    logger.log(logging.INFO, f"Connection from {addr[0]}!")

    transport = paramiko.Transport(client)
    transport.add_server_key(host_key)

    server = ChatRoomServ()
    try:
        transport.start_server(server=server)
    except Exception as ex:
        logger.log(logging.INFO, f"SSH negotiation failed for {addr[0]} failed. ({ex})")
        return

    chan = transport.accept()
    if not chan:
        logger.log(logging.INFO, f"No channel for {addr[0]}.")
        return

    server.event.wait(10)
    if not server.event.is_set():
        logger.log(logging.INFO, f"{addr[0]} never asked for a shell")
        return

    # evil and bad, but it works
    chan.usersc = UserClass()
    chan.usersc.username = transport.get_username()
    chan.usersc.usernamecolor = USER_CFG[chan.usersc.username][1]+chan.usersc.username+COLOR_RESET
    chans.append(chan)
    logger.log(logging.INFO, f"User login: {chan.usersc.username}")

    # clear, and set cursor to 2,0 and store position, before sending welcome msg
    chan.send(f"\033[2J\033[2;0fWelcome to {SERVER_NAME}!\r\n"
              f"Try typing /help!{build_status(chan)}\033[s"
             )
    send_global(context="JOIN", usercolor=chan.usersc.usernamecolor)
    threading.Thread(target=handle_user_input, args=(chan,)).start()


def run_chatroom():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((BIND_IP, PORT))

    while True:
        sock.listen(128)
        sockaddr = sock.accept()
        threading.Thread(target=init_user, args=(sockaddr,)).start()

argparser = argparse.ArgumentParser(usage=usage())

argparser.add_argument("-v", "--verbose", default=VERBOSE, action="store_true", help="verbose")
argparser.add_argument("-b", "--bindip", default=BIND_IP, help="Binding IP")
argparser.add_argument("-p", "--port", type=int, default=PORT, help="SSH Server port")
argparser.add_argument("-n", "--name", type=str, default=SERVER_NAME, help="Server name")
argparser.add_argument("-r", "--rsafile", type=str, default=RSA_PATH, help="RSA file")
argparser.add_argument("-d", "--data", type=str, default=USER_CFG_PATH, help="Pickle data file")

args = argparser.parse_args()

BIND_IP = args.bindip
PORT = args.port
SERVER_NAME = args.name
RSA_PATH = args.rsafile
USER_CFG_PATH = args.data
VERBOSE = args.verbose

host_key = paramiko.RSAKey.from_private_key_file(filename=RSA_PATH)

if os.path.exists(USER_CFG_PATH):
    with open(USER_CFG_PATH, "rb") as file:
        USER_CFG = pickle.load(file)
else:
    USER_CFG = {}

logging.basicConfig(level=logging.INFO if VERBOSE else logging.WARNING)
logger = logging.getLogger()
logger.log(logging.INFO, f"Starting chatroom {SERVER_NAME} on port {PORT}!")

run_chatroom()
