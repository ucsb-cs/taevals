import os
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError
# catch CapabilityDisabledError on model puts for downtimes

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')


class HomePage(webapp.RequestHandler):
    def get(self):
        path = os.path.join(VIEW_PATH, 'home.html')
        self.response.out.write(template.render(path, None))


class EvalPage(webapp.RequestHandler):
    def get(self, key):
        template_values = {'class':'Blah', 'key':key}
        path = os.path.join(VIEW_PATH, 'eval.html')
        self.response.out.write(template.render(path, template_values))


class ErrorPage(webapp.RequestHandler):
    def get(self):
        self.redirect('/', permanent=True)


application = webapp.WSGIApplication([('/', HomePage),
                                      (r'/eval/([a-zA-Z]+)', EvalPage),
                                      (r'/.*', ErrorPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
