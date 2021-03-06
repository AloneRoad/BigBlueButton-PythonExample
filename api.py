#! coding: utf-8
# pylint: disable-msg=W0311
from uuid import uuid4
from time import time
from random import randint
from hashlib import md5, sha1
from urllib import urlopen, quote_plus
from xml.etree import ElementTree
from redis import Redis

import settings

class XmlListConfig(list):
  def __init__(self, aList):
    for element in aList:
      if element:
        # treat like dict
        if len(element) == 1 or element[0].tag != element[1].tag:
          self.append(XmlDictConfig(element))
        # treat like list
        elif element[0].tag == element[1].tag:
          self.append(XmlListConfig(element))
      elif element.text:
        text = element.text.strip()
        if text:
          self.append(text)


class XmlDictConfig(dict):
  '''
  Example usage:

  >>> tree = ElementTree.parse('your_file.xml')
  >>> root = tree.getroot()
  >>> xmldict = XmlDictConfig(root)

  Or, if you want to use an XML string:

  >>> root = ElementTree.XML(xml_string)
  >>> xmldict = XmlDictConfig(root)

  And then use xmldict for what it is... a dict.
  '''
  def __init__(self, parent_element):
    if parent_element.items():
      self.update(dict(parent_element.items()))
    for element in parent_element:
      if element:
        # treat like dict - we assume that if the first two tags
        # in a series are different, then they are all different.
        if len(element) == 1 or element[0].tag != element[1].tag:
          aDict = XmlDictConfig(element)
        # treat like list - we assume that if the first two tags
        # in a series are the same, then the rest are the same.
        else:
          # here, we put the list in dictionary; the key is the
          # tag name the list elements all share in common, and
          # the value is the list itself 
          aDict = {element[0].tag: XmlListConfig(element)}
        # if the tag has attributes, add those to the dict
        if element.items():
          aDict.update(dict(element.items()))
        self.update({element.tag: aDict})
      # this assumes that if you've got an attribute in a tag,
      # you won't be having any text. This may or may not be a 
      # good idea -- time will tell. It works for the way we are
      # currently doing XML configuration files...
      elif element.items():
        self.update({element.tag: dict(element.items())})
      # finally, if there are no child tags and no attributes, extract
      # the text
      else:
        self.update({element.tag: element.text})
        
def xml2dict(xml_string):
  root = ElementTree.XML(xml_string)
  xmldict = XmlDictConfig(root)
  return xmldict

  
api_prefix = "http://%s/bigbluebutton/api/" % settings.server_address
db = Redis(host=settings.redis_host, port=settings.redis_port)

def get_secure_uri(api_uri):
  uri = api_uri.replace("?", "")
  checksum = sha1(uri + settings.security_salt).hexdigest()
  return api_uri + "&checksum=%s" % checksum
  
def _create(name, meeting_id, attendee_pw, moderator_pw):
  query = "name=%s&meetingID=%s&attendeePW=%s&moderatorPW=%s&logoutURL=%s" % \
          (quote_plus(name), meeting_id, attendee_pw, moderator_pw, settings.logout_url)
  api_uri = "create?%s" % query
  uri = get_secure_uri(api_uri)
  url = api_prefix + uri
  return_code = xml2dict(urlopen(url).read()).get("returncode")
  if return_code == "SUCCESS":
    return True
  return False

def _join(full_name, meeting_id, password):
  api_uri = "join?fullName=%s&meetingID=%s&password=%s" % \
            (full_name, meeting_id, password)
  uri = get_secure_uri(api_uri)
  url = api_prefix + uri
  return url

def create_meeting(name, attendee_users, moderator_users):
  attendee_pw = str(uuid4())
  moderator_pw = str(uuid4())
  while True: # get unique id
    meeting_id = randint(10000, 99999)#str(uuid4())
    key = "meeting:%s" % meeting_id
    if not db.get(key):
      break
  ok = _create(name, meeting_id, attendee_pw, moderator_pw)
  if ok:
    info = {"name": name,
            "id": meeting_id,
            "create_at": time(),
            "attendee_users": attendee_users,
            "moderator_users": moderator_users,
            "attendee_password": attendee_pw,
            "moderator_password": moderator_pw}
    key = "meeting:%s" % meeting_id
    db.set(key, str(info))
    
    key = "meeting_name:%s" % name
    db.set(key, 1)
    return meeting_id
  return False

def is_valid(name):
  key = "meeting_name:%s" % name
  if db.get(key):
    return False
  return True

def join_meeting(username, password, meeting_id):
  ok = check(username, password)
  username = username.lower()
  if ok:
    key = "meeting:%s" % meeting_id
    info = db.get(key)
    if info:
      info = eval(info)
      info = dict(info) # for hide error mark
      if username in info.get("moderator_users"):
        password = info.get("moderator_password")
      elif username in info.get("attendee_users"):
        password = info.get("attendee_password")
      else:
        return False
    
    url = _join(username, meeting_id, password)
    message = urlopen(url).read()
    if "FAILED" in message: # expired, recreate
      info = get_meeting_info(meeting_id)
      if not info:
        return False
      name = info.get("name")
      attendee_users = info.get("attendee_users")
      moderator_users = info.get("moderator_users")
      remove(meeting_id)       # remove old
      meeting_id = create_meeting(name, attendee_users, moderator_users)  # re-create     
      key = "meeting:%s" % meeting_id
      info = db.get(key)
      info = eval(info)
      info = dict(info) # for hide error mark
      if username in info.get("attendee_users"):
        password = info.get("attendee_password")
      elif username in info.get("moderator_users"):
        password = info.get("moderator_password")
      url = _join(username, meeting_id, password)
    return url
  return False

def get_meeting_info(meeting_id):
  key = "meeting:%s" % meeting_id
  info = db.get(key)
  if info:
    info = eval(info)
    return info
  
def remove(meeting_id):
  key = "meeting:%s" % meeting_id
  info = db.get(key)
  if key:
    name = eval(info).get("name")
    db.delete("meeting_name:%s" % name)
  return db.delete(key)

def update(username, meeting_id, name, attendee_users):
  key = "meeting:%s" % meeting_id
  info = db.get(key)
  if info:
    info = eval(info)
    info['name'] = name
    
    username = username.lower()
    if username not in info['moderator_users']:
      info['moderator_users'].append(username)
      
    info['attendee_users'] = attendee_users
    db.set(key, info)
    return True
  return False

def meeting_list():
  key = "meeting:*"
  keys = db.keys(key)
  if keys:
    meetings = []
    for key in keys:
      meetings.append(eval(db.get(key)))
    sorted(meetings, key=lambda k: k["create_at"], reverse=True)
    return meetings
  return None
  
def is_running(meeting_id):
  api_uri = "isMeetingRunning?meetingID=%s" % meeting_id
  api_uri = get_secure_uri(api_uri)
  url = api_prefix + api_uri
  xml_string = urlopen(url).read()
  params = xml2dict(xml_string)
  if params.get("returncode") == "SUCCESS":
    if params.get("running") == "true":
      return True
    return False
  return None

def add_user(username, password=''):
  key = "passwd:%s" % username.lower()
  if not db.get(key):
    if not password:
      password = str(uuid4())
      auto_passwd = password
      password = md5(password).hexdigest()
      db.set(key, password)
      return auto_passwd
    else:
      password = md5(password).hexdigest()
      db.set(key, password)
      return True
  return False

def remove_user(username):
  return db.delete("passwd:%s" % username.lower())

def change_password(username, new_password):
  key = "passwd:%s" % username.lower()
  password = md5(new_password).hexdigest()
  db.set(key, password)
  return True
  

def check(username, password):
  if not username or not password:
    return False
  password = md5(password).hexdigest()
  key = "passwd:%s" % username.lower()
  store_pw = db.get(key)
  if store_pw == password:
    return True
  return False

def suggest(keyword):
  key = "passwd:%s*" % keyword.lower()
  keys = db.keys(key)
  keys = [key.replace("passwd:", "") for key in keys]
  return list(set(keys))
  

if __name__ == "__main__":
  pass
