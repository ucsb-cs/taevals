import os, random
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError
# catch CapabilityDisabledError on model puts for downtimes

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')

class EvalInvite(db.Model):
    email = db.StringProperty(required=True)
    course = db.StringProperty(required=True)
    tas = db.StringListProperty(required=True)
    date = db.DateTimeProperty(auto_now_add=True)

    @staticmethod
    def create_new(student, course, tas):
        cur = None, None
        while cur != (student, course):
            key_name = hex(random.randint(0, 0xFFFFFFFF))[2:]
            tmp = EvalInvite.get_or_insert(key_name, email=student,
                                           course=course, tas=tas)
            cur = tmp.email, tmp.course
        return tmp
    

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
        by_course = {}
        for ei in EvalInvite.all():
            if ei.course in by_course:
                by_course[ei.course] += len(ei.tas)
            else:
                by_course[ei.course] = len(ei.tas)
        template_values = {'successes':successes, 'warnings':warnings,
                           'errors':errors, 'courses':by_course}
        path = os.path.join(VIEW_PATH, 'admin.html')
        self.response.out.write(template.render(path, template_values))

    def post(self):
        action = self.request.get('action')
        if not action:
            self.get()
        elif action == 'upload':
            self.upload_csv()
        elif action == 'clear':
            db.delete(EvalInvite.all())
            self.get(successes=['Cleared all invites'])
        else:
            self.get(errors=['Invalid action: %s' % action])

    def upload_csv(self):
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
            course_tas, errors = self._process_csv(tas, errors, 'TA')
        if students == '':
            errors.add('No student file submitted.')
        else:
            course_students, errors = self._process_csv(students, errors,
                                                        'student')

        s_tas = set(course_tas)
        s_students = set(course_students)
        courses = s_tas.intersection(s_students)

        for course in s_tas - courses:
            warnings.add('Skipping course with no students: %s' % course)
        for course in s_students - courses:
            warnings.add('Skipping course with no tas: %s' % course)

        db.delete(EvalInvite.all())

        if len(errors) == 0:
            for course in courses:
                for student in course_students[course]:
                    EvalInvite.create_new(student, course, course_tas[course])
                successes.append('Added %s: %d students %d tas' %
                                 (course, len(course_students[course]),
                                  len(course_tas[course])))
        self.get(successes, warnings, errors)

    @staticmethod
    def _process_csv(body, errors, descriptor):
        to_return = {}
        for line in [x.strip() for x in body.split('\n') if x != '']:
            split = [x.strip() for x in line.split(',') if x != '']
            if len(split) < 2:
                errors.add('Invalid TA file beginning at: %s' % line)
                break
            course = split[0].lower()
            name = split[1:]
                
            if course in to_return:
                errors.add('Duplicate course listing in %s file: %s' %
                           (descriptor, course))
            else:
                to_return[course] = name
        return to_return, errors


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
