#!/usr/bin/python
# -*- coding: UTF-8 -*-
import time
import threading
import xbmc, xbmcgui, xbmcaddon
tmp = time.time()

__addon__ = xbmcaddon.Addon("script.simkl")
__icon__ = __addon__.getAddonInfo("icon")
def getstr(strid): return __addon__.getLocalizedString(strid)

xbmc.log("Simkl: Icon: "+str(__icon__))

not_dialog = xbmcgui.Dialog()
def notify(txt="Test"):
  not_dialog.notification("Simkl", txt, __icon__) #Put an icon

PIN_LABEL      = 201
INSTRUCTION_ID = 202
CANCEL_BUTTON  = 203

#xmlfile = ""
#script  = __addon__.getAddonInfo("path").decode("utf-8")
class loginDialog(xbmcgui.WindowXMLDialog):
  def __init__(self, xmlFilename, scriptPath, pin, url, check_login, log, 
    exp=900, inter=5, api=None):
    self.pin = pin
    self.url = url
    self.check_login = check_login
    self.log = log
    self.exp = exp
    self.inter = inter
    self.api = api
    self.waiting = True
    self.canceled = False

  def threaded(self):
    cnt = 0
    while self.waiting:
      if cnt % (self.inter+1) == 0 and cnt>1:
        xbmc.log("Simkl: Still waiting... {}".format(cnt))
        if self.check_login(self.pin, self.log):

          xbmc.log(str(self.api.USERSETTINGS))
          notify("Hello {}".format(self.api.USERSETTINGS["user"]["name"]))
          self.waiting = False
        #Now check the user has done what it has to be done
      cnt += 1
      time.sleep(1)
      if self.canceled or cnt >= self.exp:
        self.waiting = False
        notify("Couldn't log in")

    xbmc.log("Simkl: Stop waiting")
    self.close()

  def onInit(self):
    instruction = self.getControl(INSTRUCTION_ID)
    instruction.setLabel(
      getstr(32022).format("[COLOR ffffbf00]" + self.url + "[/COLOR]"))
    self.getControl(PIN_LABEL).setLabel(self.pin)

    t = threading.Thread(target=self.threaded)
    t.start()

  def onControl(self, controlID):
    xbmc.log("Simkl: oncontrol {}".format(controlID))
  def onFocus(self, controlID):
    pass

  def onClick(self, controlID):
    xbmc.log("Simkl: onclick {}".format(controlID))
    if controlID == CANCEL_BUTTON:
      self.canceled = True

'''
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
        #Now check the user has done what it has to be done
      time.sleep(1)
      cnt += 1
      if self.dialog.iscanceled() or cnt >= exp:
        waiting = False
        #raise Not logged in
'''