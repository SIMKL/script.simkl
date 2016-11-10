#!/usr/bin/python
# -*- coding: UTF-8 -*-

import sys, os
import urllib
import request
try:
    import json
except:
    import simplejson as json

import xbmc
import interface
import httplib

REDIRECT_URI = "http://simkl.com"
with open(os.path.dirname(os.path.realpath(__file__)).strip("lib") + "data/apikey") as f:
    d = json.loads(f.read())
    APIKEY = d["apikey"]
    SECRET = d["secret"]
    xbmc.log("APIKEY: {}".format(APIKEY))
ATOKEN = 0 #Get atoken from file
headers = {"Content-Type": "application-json",
    "simkl-api-key": APIKEY
}

class API:
    def __init__(self):
        with open(os.path.dirname(os.path.realpath(__file__)).strip("lib") + "data/user", "r") as f:
            self.token = f.readline().strip("\n")
            headers["authorization"] = self.token
        self.con = httplib.HTTPSConnection("api.simkl.com")
        self.con.request("GET", "/users/settings", headers=headers)
        self.USERSETTINGS = json.loads(self.con.getresponse().read().decode("utf-8"))

    def login(self):
        url = "/oauth/pin?client_id="
        url += APIKEY + "&redirect=" + REDIRECT_URI

        log = httplib.HTTPSConnection("api.simkl.com")
        log.request("GET", url, headers=headers)
        r = log.getresponse().read().decode("utf-8")
        xbmc.log(r)
        rdic = json.loads(r)
        dialog = interface.loginDialog(rdic["verification_url"], 
            rdic["user_code"], self.check_login, log, rdic["expires_in"],
            rdic["interval"], self)

    def check_login(self, ucode, log): #Log is the connection
        global atoken
        url = "/oauth/pin/" + ucode + "?client_id=" + APIKEY
        log.request("GET", url, headers=headers)
        r = json.loads(log.getresponse().read().decode("utf-8"))
        xbmc.log("Simkl:" + str(r))
        if r["result"] == "OK":
            set_atoken(r["access_token"])
            log.request("GET", "/users/settings", headers=headers)
            r = json.loads(log.getresponse().read().decode("utf-8"))
            self.USERSETTINGS = r
            xbmc.log(str(self.USERSETTINGS))
            return True
        elif r["result"] == "KO":
            return False

    def is_user_logged(self):
        if self.token == None:
            return False
        else:
            return True

    ### SCROBBLING OR CHECKIN

    def checkin(self, filename): #OR IDMB, member: only works with movies
        if filename[:2] == "tt":
            imdb = filename
            values = {"movie":{"ids":{"imdb":filename}}}
        else:
            xbmc.log("Simkl: Filename - {}".format(filename))
            values = {"file":filename}
            values = json.dumps(values)
            self.con.request("GET", "/search/file/", body=values, headers=headers)
            r1 = self.con.getresponse().read().decode("utf-8")
            r = json.loads(r1)
            xbmc.log("Simkl: Scrobbling: {}".format(r))

            values = {r["type"]:{"ids":r[r["type"]]["ids"]}}

        #NOW THE CHECKIN
        xbmc.log("Simkl: values {}".format(values))
        values = json.dumps(values)
        self.con.request("GET", "/checkin/", body=values, headers=headers)
        xbmc.log("Simkl: {}".format(self.con.getresponse().read().decode("utf-8")))


def set_atoken(token):
    global ATOKEN
    with open(os.path.dirname(os.path.realpath(__file__)).strip("lib") + "data/user", "w") as f:
        f.write(token)
    ATOKEN = token
    headers["authorization"] = token
    self.token = token

api = API()
if __name__ == "__main__":
    if sys.argv[1] == "login":
        xbmc.log("Logging in", level=xbmc.LOGDEBUG)
        api.login()
    if sys.argv[1] == "test":
        api.scrobble_from_filename("South Park S01E02")