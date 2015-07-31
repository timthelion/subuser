#!/usr/bin/env python
# This file should be compatible with both Python 2 and 3.
# If it is not, please file a bug report.

"""
An x11 bridge provides a secure/firewalled link between a desktop application and the host x11 server. In this case, we use XPRA to do the bridging.

::.

  -------------                      -------------
  |desktop app| <--/tmp/.X11-unix--> |xpra server|    Untrusted
  -------------                      -------------
                                           ^
                                           | ~/.xpra
                                           v
  -------------                       -------------
  | host      |  <--/tmp/.X11-unix--> |xpra client|   Trusted
  -------------                       -------------

This configuration involves 3 containers.

1) contains the untrusted desktop application
2) contains an untrusted xpra server
3) contains a trusted xpra client

I up-to-date version of xpra can be used, xpra need not be installed on the host.

"""

#external imports
import os
import time
import signal
import shutil
#internal imports
from subuserlib.classes.userOwnedObject import UserOwnedObject
from subuserlib.classes.service import Service
import subuserlib.verify as verify
import subuserlib.subuser

class XpraX11Bridge(Service):
  __subuser = None

  def __init__(self,user,subuser):
    Service.__init__(self,user,subuser)
    self.__subuser = subuser

  def getName(self):
    return "xpra"

  def getSubuser(self):
    return self.__subuser

  def isSetup(self):
    clientSubuserInstalled = self.getClientSubuserName() in self.getUser().getRegistry().getSubusers()
    serverSubuserInstalled = self.getServerSubuserName() in self.getUser().getRegistry().getSubusers()
    return clientSubuserInstalled and serverSubuserInstalled

  def setup(self):
    """
    Do any setup required in order to create a functional bridge: Creating subusers building images ect.
    """
    if not self.isSetup():
      self.getUser().getRegistry().setLogOutputVerbosity(0)
      self.addServerSubuser()
      self.addClientSubuser()
      verify.verify(self.getUser())
      self.getServerSubuser().getPermissions()["system-dirs"] = {self.getServerSideX11Path():"/tmp/.X11-unix",self.getXpraDir():os.path.join(os.getenv("HOME"))}
      self.getServerSubuser().getPermissions().save()
      self.getClientSubuser().getPermissions()["system-dirs"] = {self.getXpraDir():os.path.join(os.getenv("HOME"))}
      self.getClientSubuser().getPermissions().save()
  
  def getServerSideX11Path(self):
    return os.path.join(self.getUser().getConfig()["volumes-dir"],"xpra",self.getSubuser().getName(),"tmp",".X11-unix")

  def getXpraDir(self):
    return os.path.join(self.getUser().getConfig()["volumes-dir"],"xpra",self.getSubuser().getName(),"xpra-home")

  def getServerSubuserName(self):
    return "!service-subuser-"+self.getSubuser().getName()+"-xpra-server"

  def getServerSubuser(self):
    return self.getUser().getRegistry().getSubusers()[self.getServerSubuserName()]

  def addServerSubuser(self):
    subuserlib.subuser.addFromImageSource(self.getUser(),self.getServerSubuserName(),self.getUser().getRegistry().getRepositories()["default"]["subuser-internal-xpra-server"])
    self.getSubuser().addServiceSubuser(self.getServerSubuserName())

  def getClientSubuserName(self):
    return "!service-subuser-"+self.getSubuser().getName()+"-xpra-client"

  def getClientSubuser(self):
    return self.getUser().getRegistry().getSubusers()[self.getClientSubuserName()]

  def addClientSubuser(self):
    subuserlib.subuser.addFromImageSource(self.getUser(),self.getClientSubuserName(),self.getUser().getRegistry().getRepositories()["default"]["subuser-internal-xpra-client"])
    self.getSubuser().addServiceSubuser(self.getClientSubuserName())

  def cleanUp(self):
    """
    Clear special volumes. This ensures statelessness of stateless subusers.
    """
    try:
      shutil.rmtree(self.getServerSideX11Path(), ignore_errors=True)
    except OSError:
      pass
    try:
      shutil.rmtree(self.getXpraDir(), ignore_errors=True)
    except OSError:
      pass

  def start(self,serviceStatus):
    """
    Start the bridge.
    """
    self.cleanUp()
    # Create and setup special volumes.
    try:
      os.makedirs(self.getServerSideX11Path())
    except OSError:
      pass
    try:
      os.makedirs(self.getXpraDir())
    except OSError:
      pass
    os.chmod(self.getServerSideX11Path(),1023)

    permissionDict = {
     "system-tray": ("--system-tray" , "--no-system-tray"),
     "cursors": ("--cursors", "--no-cursors"),
     "clipboard": ("--clipboard","--no-clipboard")}
    permissionArgs = []
    for guiPermission,(on,off) in permissionDict.items():
      if self.getSubuser().getPermissions()["gui"][guiPermission]:
        permissionArgs.append(on)
      else:
        permissionArgs.append(off)
    commonArgs = ["--no-daemon","--no-notifications"]
    # Launch xpra server
    serverArgs = ["start","--no-pulseaudio","--no-mdns","--encoding=rgb"]
    serverArgs.extend(commonArgs)
    serverArgs.extend(permissionArgs)
    serverArgs.append(":100")
    serverRuntime = self.getServerSubuser().getRuntime(os.environ)
    serverRuntime.setBackground(True)
    serverContainer = serverRuntime.run(args=serverArgs)
    serviceStatus["xpra-server-service-cid"] = serverContainer.getId()
    self.waitForServerContainerToLaunch()
    serverContainerInfo = serverContainer.inspect()
    if serverContainerInfo is None:
      exit("The xpra server container failed to launch. Container id:" + serverContainer.getId())
    # Launch xpra client
    clientArgs = ["attach","--no-tray","--compress=0","--encoding=rgb"]
    clientArgs.extend(commonArgs)
    clientArgs.extend(permissionArgs)
    clientRuntime = self.getClientSubuser().getRuntime(os.environ)
    clientRuntime.setEnvVar("XPRA_SOCKET_HOSTNAME",serverContainerInfo["Config"]["Hostname"])
    clientRuntime.setBackground(True)
    serviceStatus["xpra-client-service-cid"] = clientRuntime.run(args=clientArgs).getId()
    return serviceStatus

  def waitForServerContainerToLaunch(self):
    while True:
      if os.path.exists(os.path.join(self.getServerSideX11Path(),"X100")):
        return
      time.sleep(0.05)

  def stop(self,serviceStatus):
    """
    Stop the bridge.
    """
    self.getUser().getDockerDaemon().getContainer(serviceStatus["xpra-client-service-cid"]).stop()
    self.getUser().getDockerDaemon().getContainer(serviceStatus["xpra-server-service-cid"]).stop()
    self.cleanUp()

def X11Bridge(user,subuser):
  return bridges[user.getConfig()["x11-bridge"]](user,subuser)

bridges = {"xpra":XpraX11Bridge}
