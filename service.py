#!/usr/bin/python
# -*- coding: UTF-8 -*-
'''
NOT LICENSED YET
Creator: David Davó <david@ddavo.me>
'''

import sys, os
import xbmcaddon
import xbmc
import resources
from resources.lib import interface, engine
from resources.lib import simklapi as API
from resources.lib.utils import *

__addon__ = xbmcaddon.Addon()
interface.__addon__ = __addon__
autoscrobble = __addon__.getSetting("autoscrobble")

try:
    compdatefile = os.path.join(__addon__.getAddonInfo("path").decode("utf-8"), "resources", "data", "compdate.txt")
    with open(xbmc.translatePath(compdatefile), "r") as f:
        __compdate__ = f.read()
except IOError:
    __compdate__ = "ERROR: No such file or directory"

class Monitor(xbmc.Monitor):
    """ http://mirrors.kodi.tv/docs/python-docs/16.x-jarvis/xbmc.html#Monitor """
    def __init__(self, engine):
        self.engine = engine

    def onScanFinished(self, arg):
        """ When library scan finishes """
        ### Connect with config
        xbmc.log("Simkl: onScanFinished {0}".format(str(arg)))
        if bool(getSetting("synclib")): self.engine.synclibrary()

if __name__ == "__main__":
    xbmc.log("Simkl dir: " + str(xbmc.translatePath("special://home")))
    xbmc.log("Simkl | Python Version: " + str(sys.version))
    xbmc.log("Simkl | "+ str(sys.argv), level=xbmc.LOGDEBUG)
    xbmc.log("Simkl | compdate: {0}".format(__compdate__))

    player  = engine.Player()
    player.addon = __addon__
    eng     = engine.Engine(API.api, player)
    monitor = Monitor(eng)


    if not API.api.is_user_logged():
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
