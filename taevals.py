import os
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError
# catch CapabilityDisabledError on model puts for downtimes

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')


class EvaluationInvitation(db.Model):
    secret_key = db.IntegerProperty(required=True)
    email = db.StringProperty(required=True)
    course = db.StringProperty(required=True)
    tas_to_eval = db.StringListProperty(required=True)
    date = db.DateTimeProperty(auto_now_add=True)
    

class HomePage(webapp.RequestHandler):
    def get(self):
        path = os.path.join(VIEW_PATH, 'home.html')
        self.response.out.write(template.render(path, None))


class EvalPage(webapp.RequestHandler):
    def get(self, key):
        template_values = {'class':'Blah', 'key':key}
        path = os.path.join(VIEW_PATH, 'eval.html')
        self.response.out.write(template.render(path, template_values))


class AdminPage(webapp.RequestHandler):
    def get(self, successes=None, warnings=None, errors=None):
        template_values = {'successes':successes, 'warnings':warnings,
                           'errors':errors}
        path = os.path.join(VIEW_PATH, 'admin.html')
        self.response.out.write(template.render(path, template_values))

    def post(self):
        tas = self.request.get('tas')
        students = self.request.get('students')

        course_tas = {}
        course_students = {}
        errors = set()
        warnings = set()
        successes = []

        if tas == '':
            errors.add('No TA file submitted.')
        else:
            for line in [x.strip() for x in tas.split('\n') if x != '']:
                split = [x.strip() for x in line.split(',') if x != '']
                if len(split) < 2:
                    errors.add('Invalid TA file beginning at: %s' % line)
                    break
                course = split[0].lower()
                tas = split[1:]
                
                if course in course_tas:
                    errors.add('Duplicate course listing in TA file: %s' %
                               course)
                else:
                    course_tas[course] = tas

        if students == '':
            errors.add('No student file submitted.')
        else:
            for line in [x.strip() for x in students.split('\n') if x != '']:
                split = [x.strip() for x in line.split(',') if x != '']
                if len(split) < 2:
                    errors.add('Invalid student file beginning at: %s' % line)
                    break
                course = split[0].lower()
                students = split[1:]

                if course not in course_tas:
                    warnings.add('Skipping course with no TAs: %s' % course)
                elif course in course_students:
                    errors.add('Duplicate course listing in TA file: %s' %
                               course)
                    break
                else:
                    course_students[course] = students

        for course in set(course_tas) - set(course_students):
            warnings.add('Skipping course with no students: %s' % course)

        if len(errors) == 0:
            for course, students in course_students.items():
                successes.append('Added: %s with %d students and %d tas' %
                                 (course, len(students),
                                  len(course_tas[course])))

        self.get(successes, warnings, errors)


class ErrorPage(webapp.RequestHandler):
    def get(self):
        self.redirect('/', permanent=True)


application = webapp.WSGIApplication([('/', HomePage),
                                      (r'/eval/([a-zA-Z0-9]+)', EvalPage),
                                      ('/admin', AdminPage),
                                      (r'/.*', ErrorPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
