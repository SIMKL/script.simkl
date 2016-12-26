#!/usr/bin/python
# -*- coding: UTF-8 -*-

import sys, os, time
import urllib
import request
try:
    import json
except:
    import simplejson as json

import xbmc
import interface
import httplib

__addon__ = interface.__addon__
def getstr(id): return interface.getstr(id)

REDIRECT_URI = "http://simkl.com"
USERFILE     = xbmc.translatePath("special://profile/simkl_key")
if not os.path.exists(USERFILE):
    with open(USERFILE, "w") as f:
        f.write("")
else:
    with open(USERFILE, "r") as f:
        print(xbmc.log("Simkl Userfile " + str(f.read())))

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
        with open(USERFILE, "r") as f:
            self.token = f.readline().strip("\n")
            headers["authorization"] = "Bearer " + self.token
        try:
            self.con = httplib.HTTPSConnection("api.simkl.com")
            self.con.request("GET", "/users/settings", headers=headers)
            self.USERSETTINGS = json.loads(self.con.getresponse().read().decode("utf-8"))
            self.internet = True
            if not os.path.exists(USERFILE):
                api.login()
        except Exception:
            xbmc.log("Simkl: {}".format("No INTERNET"))
            interface.notify(getstr(32027))
            self.internet = False

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

    def set_atoken(self, token):
        global ATOKEN
        with open(USERFILE, "w") as f:
            f.write(token)
        ATOKEN = token
        headers["authorization"] = token
        self.token = token

    def check_login(self, ucode, log): #Log is the connection
        global atoken
        url = "/oauth/pin/" + ucode + "?client_id=" + APIKEY
        log.request("GET", url, headers=headers)
        r = json.loads(log.getresponse().read().decode("utf-8"))
        xbmc.log("Simkl:" + str(r))
        if r["result"] == "OK":
            self.set_atoken(r["access_token"])
            log.request("GET", "/users/settings", headers=headers)
            r = json.loads(log.getresponse().read().decode("utf-8"))
            self.USERSETTINGS = r
            xbmc.log(str(self.USERSETTINGS))
            return True
        elif r["result"] == "KO":
            return False

    def is_user_logged(self):
        if self.token == "":
            xbmc.log("Simkl: User not logged in")
            return False
        else:
            return True

    ### SCROBBLING OR CHECKIN

    def watched(self, filename, mediatype, date=time.strftime('%Y-%m-%d %H:%M:%S')): #OR IDMB, member: only works with movies
        if self.is_user_logged():
            try:
                if mediatype == "unknown": mediatype = "episode"
                con = httplib.HTTPSConnection("api.simkl.com")
                mediadict = {"movie": "movies", "episode":"episodes"}
                media = mediadict[mediatype]
                tosend = {}
                
                if filename[:2] == "tt":
                    imdb = filename
                    toappend = {"ids":{"imdb":filename}, "watched_at":date}
                else:
                    xbmc.log("Simkl: Filename - {}".format(filename))
                    values = {"file":filename}
                    values = json.dumps(values)
                    con.request("GET", "/search/file/", body=values, headers=headers)
                    r1 = con.getresponse().read().decode("utf-8")
                    r = json.loads(r1)
                    xbmc.log("Simkl: {}".format(r))
                    toappend = {"ids": r[mediatype]["ids"], "watched_at":date}

                tosend[media] = []
                tosend[media].append(toappend)
                tosend = json.dumps(tosend)

                xbmc.log("Simkl: values {}".format(tosend))
                con.request("GET", "/sync/history/", body=tosend, headers=headers)
                r = con.getresponse().read().decode("utf-8")
                xbmc.log("Simkl: {}".format(r))

                return max(json.loads(r)["added"].values())

            except httplib.BadStatusLine:
                xbmc.log("Simkl: {}".format("ERROR: httplib.BadStatusLine"))
        else:
            xbmc.log("Simkl: Can't scrobble. User not logged in")
            return 0

api = API()
if __name__ == "__main__":
    if sys.argv[1] == "login":
        xbmc.log("Logging in", level=xbmc.LOGDEBUG)
        api.login()
    if sys.argv[1] == "test":
        api.scrobble_from_filename("South Park S01E02")