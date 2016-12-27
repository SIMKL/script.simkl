#!/usr/bin/python
# -*- coding: UTF-8 -*-
'''
NOT LICENSED YET
Creator: David Davó <david@ddavo.me>
'''

import sys
import xbmcaddon
import xbmc
from resources.lib import interface, engine
from resources.lib import simklapi as API

simkl_addon = xbmcaddon.Addon()
interface.__addon__ = simkl_addon
autoscrobble = simkl_addon.getSetting("autoscrobble")
def getstr(strid): return simkl_addon.getLocalizedString(strid)

if __name__ == "__main__":
  xbmc.log("Simkl dir: " + str(xbmc.translatePath("special://home")))
  xbmc.log("Simkl | Python Version: " + str(sys.version))
  xbmc.log("Simkl | "+ str(sys.argv), level=xbmc.LOGDEBUG)
  monitor = xbmc.Monitor()

  player  = engine.Player()
  player.addon = simkl_addon
  eng     = engine.Engine(API.api, player)
  #Remember: if getTime() is more than x% scrobble file

  if not API.api.is_user_logged():
    interface.notify(getstr(32026))
  else:
    interface.notify(getstr(32025).format(API.api.USERSETTINGS["user"]["name"]))

  while not monitor.abortRequested():
    if monitor.waitForAbort(60):
      break
    elif player.isPlaying():
      player.onPlayBackStopped()