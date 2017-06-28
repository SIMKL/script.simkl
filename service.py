#!/usr/bin/python
# -*- coding: UTF-8 -*-
'''
NOT LICENSED YET
Creator: David Davó <david@ddavo.me>
'''

import sys, os
import xbmcaddon
import xbmc
import xbmcgui

from threading import Timer
WINDOW = xbmcgui.Window(10000)
if WINDOW.getProperty("SimklTrackerRun") == "True":
    xbmc.log('Simkl: already started')
    sys.exit(0)
WINDOW.setProperty("SimklTrackerRun", "True")

def stop_singleton():
    WINDOW.clearProperty("SimklTrackerRun")
t = Timer(5, stop_singleton)
t.start()

from resources.lib import interface, engine
from resources.lib import simklapi as API

__addon__ = xbmcaddon.Addon()
interface.__addon__ = __addon__
autoscrobble = __addon__.getSetting("autoscrobble")
def getstr(strid): return __addon__.getLocalizedString(strid)

try:
    compdatefile = os.path.join(__addon__.getAddonInfo("path").decode("utf-8"), "resources", "data", "compdate.txt")
    with open(xbmc.translatePath(compdatefile), "r") as f:
        __compdate__ = f.read()
except:
    __compdate__ = "ERROR: No such file or directory"

if __name__ == "__main__":
    xbmc.log("Simkl dir: " + str(xbmc.translatePath("special://home")))
    xbmc.log("Simkl | Python Version: " + str(sys.version))
    xbmc.log("Simkl | "+ str(sys.argv), level=xbmc.LOGDEBUG)
    xbmc.log("Simkl | compdate: {0}".format(__compdate__))
    monitor = xbmc.Monitor()

    player  = engine.Player()
    player.addon = __addon__
    eng     = engine.Engine(API.api, player)
    #Remember: if getTime() is more than x% scrobble file

    #Testing:
    #API.api.login()

    if API.api.internet == False:
        interface.notify(getstr(32027))
    elif not API.api.is_user_logged():
        API.api.login() #Add "remind me tomorrow button"
        #interface.notify(getstr(32026))
    else:
        interface.notify(getstr(32025).format(API.api.USERSETTINGS["user"]["name"]))

    #__addon__.openSettings()
    while not monitor.abortRequested():
        if monitor.waitForAbort(90):
            break
        elif player.isPlaying():
            player.onPlayBackStopped()
