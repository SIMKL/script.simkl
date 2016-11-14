#!/usr/bin/python
# -*- coding: UTF-8 -*-
'''
NOT LICENSED YET
Creator: David Davó <david@ddavo.me>
'''

import time, sys
import xbmcaddon
import xbmc
from resources.lib import interface, engine
from resources.lib import simklapi as API

simkl_addon = xbmcaddon.Addon()
autoscrobble = simkl_addon.getSetting("autoscrobble")

if __name__ == "__main__":
    xbmc.log("Simkl | "+ str(sys.argv), level=xbmc.LOGDEBUG)
    monitor = xbmc.Monitor()

    player  = engine.Player()
    player.addon = simkl_addon
    eng     = engine.Engine(API.api, player)
    #Remember: if getTime() is more than x% scrobble file

    if not API.api.is_user_logged():
        interface.notify("Please log in")
    else:
        interface.notify("Hello again {}".format(API.api.USERSETTINGS["user"]["name"]))

    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break
        elif player.isPlaying():
            #xbmc.log("Simkl: " + player.getPlayingFile(), level=xbmc.LOGDEBUG)
            pass