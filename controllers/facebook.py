import datetime
import json
import logging
import urllib

import tornado.auth
import tornado.escape
import tornado.web

from base import BaseHandler
from logic import content_remote

# This monkeypatches tornado to do sync instead of async
class FacebookHandler(BaseHandler,
                      tornado.auth.FacebookGraphMixin):

  def get(self):
    if not self.authenticate(author=True):
      return

    if self.get_argument("get_feed", None):
      access_token = self.get_author_user().facebook
      self.facebook_request(
            "/me/home",
            access_token=access_token, callback=self.timeline_result)
      return
    elif self.get_argument("code", False):
      self.get_sync_authenticated_user(
        redirect_uri=self.nav_url(host=True, section='facebook'),
        client_id=self.settings["facebook_api_key"],
        client_secret=self.settings["facebook_secret"],
        code=self.get_argument("code"),
        callback=self.async_callback(
          self._on_auth))
      return
    self.authorize_redirect(redirect_uri=self.nav_url(host=True, section='facebook'),
                            client_id=self.settings["facebook_api_key"],
                            extra_params={"scope": "read_stream,publish_stream" })

  def timeline_result(self, response):
    posts = response['data']
    for post in posts:
      exists = self.models.content_remote.get(to_username=self.user.username, type='facebook', post_id=post['id'])[0]

      date_updated = None
      if post.has_key('updated_time'):
        date_updated = datetime.datetime.strptime(post['updated_time'][:-5], '%Y-%m-%dT%H:%M:%S')

      if exists:
        if date_updated and date_updated != exists.date_updated:
          new_post = exists
        else:
          continue
      else:
        new_post = self.models.content_remote()

      new_post.to_username = self.user.username
      new_post.username = post['from']['name']
      new_post.from_user = post['actions'][0]['link']
      #new_post.avatar = tweet['user']['profile_image_url']

      parsed_date = datetime.datetime.strptime(post['created_time'][:-5], '%Y-%m-%dT%H:%M:%S')

      # we don't keep items that are over 30 days old
      if parsed_date < datetime.datetime.utcnow() - datetime.timedelta(days=self.constants['feed_max_days_old']):
        continue

      new_post.date_created = parsed_date
      new_post.date_updated = date_updated
      new_post.comments_count = 0
      new_post.comments_updated = None
      new_post.type = 'facebook'
      new_post.title = ''
      new_post.post_id = post['id']
      new_post.link = post['actions'][0]['link']
      view = ""
      if post.has_key('picture'):
        view += '<img src="' + post['picture'] + '">'
      if post.has_key('message'):
        view += post['message']
      if post.has_key('caption'):
        view += post['caption']
      if post.has_key('description'):
        view += post['description']
      new_post.view = view
      new_post.save()

    count = self.models.content_remote.get(to_username=self.user.username, type='facebook', deleted=False).count()
    self.write(json.dumps({ 'count': count }))

  def facebook_request(self, path, callback, access_token=None,
                       post_args=None, **args):
    """Fetches the given relative API path, e.g., "/btaylor/picture"

    If the request is a POST, post_args should be provided. Query
    string arguments should be given as keyword arguments.

    An introduction to the Facebook Graph API can be found at
    http://developers.facebook.com/docs/api

    Many methods require an OAuth access token which you can obtain
    through authorize_redirect() and get_authenticated_user(). The
    user returned through that process includes an 'access_token'
    attribute that can be used to make authenticated requests via
    this method. Example usage::

        class MainHandler(tornado.web.RequestHandler,
                          tornado.auth.FacebookGraphMixin):
            @tornado.web.authenticated
            @tornado.web.asynchronous
            def get(self):
                self.facebook_request(
                    "/me/feed",
                    post_args={"message": "I am posting from my Tornado application!"},
                    access_token=self.current_user["access_token"],
                    callback=self.async_callback(self._on_post))

            def _on_post(self, new_entry):
                if not new_entry:
                    # Call failed; perhaps missing permission?
                    self.authorize_redirect()
                    return
                self.finish("Posted a message!")

    """
    url = "https://graph.facebook.com" + path
    all_args = {}
    if access_token:
      all_args["access_token"] = access_token
      all_args.update(args)
      all_args.update(post_args or {})
    if all_args:
      url += "?" + urllib.urlencode(all_args)

    response = content_remote.get_url(url, post=(post_args is not None))
    self._on_facebook_request(callback, response)

  def get_sync_authenticated_user(self, redirect_uri, client_id, client_secret,
                                  code, callback, extra_fields=None):
    args = {
      "client_id": client_id,
      "client_secret": client_secret,
      "code": code,
      "redirect_uri": redirect_uri,
    }

    response = content_remote.get_url(self._oauth_request_token_url(**args), post=True)
    self._on_access_token(callback, response)

  def _on_access_token(self, callback, response):
    if response.error:
      logging.warning("Could not fetch access token")
      callback(None)
      return

    args = tornado.escape.parse_qs_bytes(tornado.escape.native_str(response.body))
    access_token = args["access_token"][-1]
    callback(access_token)

  def _on_auth(self, access_token):
    if not access_token:
      raise tornado.web.HTTPError(500, "Facebook auth failed")

    user = self.get_author_user()
    user.facebook = access_token
    user.save()

    self.redirect(self.nav_url(section='dashboard'))
