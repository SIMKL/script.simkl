#!/usr/bin/python
# -*- coding: UTF-8 -*-
import xbmc, xbmcgui, xbmcaddon
import time
tmp = time.time()

__addon__ = xbmcaddon.Addon("script.service.simkl")
__icon__ = __addon__.getAddonInfo("icon")
def getstr(id): return __addon__.getLocalizedString(id)

xbmc.log("Simkl: Icon: "+str(__icon__))

not_dialog = xbmcgui.Dialog()
def notify(txt="Test"):
    not_dialog.notification("Simkl", txt, __icon__) #Put an icon

import simklapi
    
class loginDialog:
    def __init__(self, url, pin, check_login, log, exp=900, inter=5, api=None):
        #TODO: If user is loged in, show a confirmation dialog
        API = api
        self.dialog = xbmcgui.DialogProgress()
        self.dialog.create(getstr(32021), 
            getstr(32022).format(url), "PIN: {}".format(pin))
        waiting = True
        cnt = 0
        while waiting:
            pct = min(max(1, int( round( cnt*100 / exp, 0))), 99)
            self.dialog.update(pct, line3=getstr(32023).format(
                str(cnt).zfill(3), exp))

            if cnt % (inter+1) == 0 and cnt>1:
                if check_login(pin,log):
                    dialognot = xbmcgui.Dialog()
                    xbmc.log(str(API.USERSETTINGS))
                    dialognot.notification("Simkl", "Hello {}".format(
                        API.USERSETTINGS["user"]["name"]))
                    waiting = False
                pass #Now check the user has done what it has to be done
            time.sleep(1)
            cnt += 1
            if self.dialog.iscanceled() or cnt >= exp:
                waiting = False
                #raise Not logged in
