import cgi, os, pickle, random, urllib
from google.appengine.api import mail, users
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app

#from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError
# catch CapabilityDisabledError on model puts for downtimes
# Test resource http://pastebin.com/bbidrU7g

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')
EMAIL_SUBJECT = 'Computer Science Midterm TA Evaluations'
EMAIL_TEMPLATE = """Student,

You are receiving this email because you are currently enrolled in the
following Computer Science Department courses. Please submit an evaluation for
at least one TA in each course you are enrolled in.

%s

Thank You,
%s"""

QUESTIONS = [
    ('Please rate your TA\'s knowledge of the course subject matter.', 0),
    ('Please rate your TA\'s preparation for discussion section.', 0),
    ('How effective are your TA\'s English communication skills?', 0),
    ('Please rate the quality of your TA\'s board work.', 0),
    ('How effective is your TA in answering students\' questions?', 0),
    ('Please rate the overall effectiveness of your TA.', 0),
    ('How often do you attend the discussion section / lab?', 1),
    (''.join(['Please describe at least one specific strength of your TA, ',
              'discussion section or lab.']), 2),
    (''.join(['Please suggest at least one specific improvement for your TA, ',
              'discussion or lab.']), 2)]
Q_KEY = ['(1) Exceptional  (2) Good  (3) Average  (4) Fair  (5) Poor',
         '(1) Always  (2) Sometimes  (3) Occasionally  (4) Seldom  (5) Never']
Q_H = '        weight:  (1)  (2)  (3)  (4)  (5) | Blank  Total  Mean  Median\n'


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


class Eval(db.Model):
    ta = db.StringProperty(required=True)
    course = db.StringProperty(required=True)
    responses = db.BlobProperty(required=True)

    def get_responses(self):
        return pickle.loads(self.responses)

    def update_response_list(self, responses):
        current = pickle.loads(self.responses)

        for i, (_, q_type) in enumerate(QUESTIONS):
            response = responses[i].strip()
            if not response: continue

            if q_type in [0, 1]:
                current[i][int(response)] += 1
            else:
                current[i].append(response)
        self.responses = pickle.dumps(current, pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def create_or_update(ta, course, responses):
        key_name = '%s-%s' % (ta, course)
        obj = Eval.get_by_key_name(key_name)
        if not obj:
            response_list = Eval._construct_response_list()
            obj = Eval(key_name=key_name, ta=ta, course=course,
                       responses=response_list)
        obj.update_response_list(responses)
        obj.put()

    @staticmethod
    def formatted_question_stats(values):
        total = sum(values)
        tmp = []
        responded = total - values[0]
        s = ' ' * 16
        for i, count in enumerate(values[1:]):
            if count:
                s += '%3.0f%% ' % (count * 100. / responded)
                tmp.extend([i+1 for _ in range(count)])
            else:
                s += ' ' * 5

        mean = sum(tmp) * 1. / responded
        if len(tmp) % 2 == 0:
            median = (tmp[len(tmp) / 2] + tmp[len(tmp) / 2 - 1]) / 2.
        else:
            median = tmp[len(tmp) / 2]
        s += '| %4d   %4d   %4.1f   %4.1f\n\n' % (values[0], total, mean,
                                                 median)
        return s

    @staticmethod
    def generate_summary(evals):
        responses = evals[0].get_responses()
        for eval in evals[1:]:
            tmp = eval.get_responses()
            for q_num, (_, q_type) in enumerate(QUESTIONS):
                if q_type in [0, 1]:
                    for r_num in range(len(tmp[q_num])):
                        responses[q_num][r_num] += tmp[q_num][r_num]
                else:
                    responses[q_num].extend(tmp[q_num])
                    
        s = ''
        for q_num, (question, q_type) in enumerate(QUESTIONS):
            s += '    %2d. %s\n' % (q_num+1, question)
            if q_type in [0, 1]:
                s += '        %s\n' % Q_KEY[q_type]
                s += Q_H
                s += Eval.formatted_question_stats(responses[q_num])
            else:
                for i, res in enumerate(sorted(responses[q_num])):
                    if '\n' in res:
                        s += '%5s %3d. %s\n' % ('', i + 1,
                                                res.replace('\n',
                                                            '\n%11s' % ''))
                    else:
                        s += '%5s %3d. %s\n' % ('', i+1, res)
                s += '\n'
        return s

    @staticmethod
    def _construct_response_list():
        responses = []
        for _, q_type in QUESTIONS:
            if q_type in [0, 1]:
                responses.append([0, 0, 0, 0, 0, 0])
            else:
                responses.append([])
        return pickle.dumps(responses, pickle.HIGHEST_PROTOCOL)


class HomePage(webapp.RequestHandler):
    def get(self):
        path = os.path.join(VIEW_PATH, 'home.html')
        self.response.out.write(template.render(path, None))


class EvalPage(webapp.RequestHandler):
    def get(self, key, ta='', responses=None, success=None, errors=None):
        ei = EvalInvite.get_by_key_name(key)
        if ei:
            if not responses:
                responses = [''] * len(QUESTIONS)
            questions = zip(QUESTIONS, responses)

            template_values = {'ei':ei, 'success':success, 'errors':errors,
                               'sel_ta':ta, 'questions':questions}
            path = os.path.join(VIEW_PATH, 'eval.html')
            self.response.out.write(template.render(path, template_values))
        else:
            self.redirect('/')

    def post(self, key):
        ei = EvalInvite.get_by_key_name(key)
        if not ei: return self.redirect('/')

        errors = []

        ta = self.request.get('ta')
        if ta not in ei.tas:
            errors.append('Must select a TA to evaluate')
            ta = ''

        responses = self.request.get_all('response')

        for i in range(len(QUESTIONS)):
            if i > len(responses):
                responses[i] = ''
                continue
            if QUESTIONS[i][1] in [0, 1]:
                if responses[i] not in ['0', '1', '2', '3', '4', '5']:
                    responses[i] = ''
                    errors.append('Must provide an answer for "%s"' %
                                  QUESTIONS[i][0])
        if errors:
            return self.get(key, ta, responses, errors=errors)

        db.run_in_transaction(Eval.create_or_update, ta, ei.course, responses)

        # Remove TA from list of TAs student can evaluate
        ei.tas.remove(ta)
        ei.put()

        self.get(key, success='Evaluated: %s' % ta)


class AdminStatPage(webapp.RequestHandler):
    def get(self, course=None, ta=None):
        evals = []
        if ta != None:
            ta = urllib.unquote(ta)
            course = urllib.unquote(course)
            if course == None: return self.redirect('/admin')
            key_name = '%s-%s' % (ta, course)
            obj = Eval.get_by_key_name(key_name)
            if obj == None:
                return self.redirect('/admin')
            evals.append(obj)
            title = ta
        elif course != None:
            course = urllib.unquote(course)
            evals.extend([x for x in Eval.all().filter('course =', course)])
            title = course
        else:
            evals.extend([x for x in Eval.all()])
            title = 'all'

        if not evals:
            return self.redirect('/admin')

        results = Eval.generate_summary(evals)

        if self.request.get('dl') == '1':
            self.response.headers['Content-Type'] = 'text/plain'
            content_disposition = 'attachement; filename="%s.txt"' % title
            self.response.headers['Content-Disposition'] = content_disposition
            self.response.out.write(results)
        else:
            path = os.path.join(VIEW_PATH, 'results.html')
            dl_url = '%s?dl=1' % self.request.url
            template_values = {'results':results, 'title':title,
                               'dl_url':dl_url}
            self.response.out.write(template.render(path, template_values))


class AdminPage(webapp.RequestHandler):
    def get(self, successes=None, warnings=None, errors=None):
        courses = {}

        # Remaining Evaluations
        for ei in EvalInvite.all():
            if ei.course not in courses:
                courses[ei.course] = {}

            for ta in ei.tas:
                if ta in courses[ei.course]:
                    courses[ei.course][ta].remaining += 1
                else:
                    courses[ei.course][ta] = Dummy(remaining=1, completed=0)

        # Completed Evaluations
        for e in Eval.all():
            completed = sum(e.get_responses()[0])
            if e.ta in courses[e.course]:
                courses[e.course][e.ta].completed = completed
            else:
                courses[e.course][e.ta] = Dummy(completed=completed,
                                                remaining=0)

        template_values = {'successes':successes, 'warnings':warnings,
                           'errors':errors, 'courses':courses,
                           'admin':users.get_current_user()}
        path = os.path.join(VIEW_PATH, 'admin.html')
        self.response.out.write(template.render(path, template_values))

    def post(self):
        action = self.request.get('action')
        if not action:
            self.get()
        elif action == 'email':
            self.email_invites()
        elif action == 'upload':
            self.upload_csv()
        elif action == 'reset':
            self.reset()
        else:
            self.get(errors=['Invalid action: %s' % action])

    def email_invites(self):
        if self.request.get('name') == '':
            return self.get(errors=['From name cannot be blank'])
        else:
            nickname = self.request.get('name')
        students = {}
        for ei in EvalInvite.all():
            if not ei.tas: continue

            if ei.email in students:
                students[ei.email][ei.course] = (ei.key().name(), ei.tas)
            else:
                students[ei.email] = {ei.course:(ei.key().name(), ei.tas)}

        for email, courses in students.items():
            output = ''
            for course in sorted(courses):
                key, tas = courses[course]
                url = 'https://%s/eval/%s' % (os.environ['HTTP_HOST'], key)
                output += '\n'.join(['-%s' % course,
                                     '\tTAs: %s' % ', '.join(sorted(tas)),
                                     '\tURL: %s' % url, ''])
            user = users.get_current_user()
            email_from = '%s <%s>' % (nickname,
                                      user.email())
            mail.send_mail(email_from, email, EMAIL_SUBJECT,
                           EMAIL_TEMPLATE % (output, nickname))
        self.get(['Sent %d emails from %s' % (len(students),
                                              cgi.escape(email_from))])

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

        if len(errors) == 0:
            for course in courses:
                for student in course_students[course]:
                    EvalInvite.create_new(student, course, course_tas[course])
                successes.append('Added %s: %d students %d tas' %
                                 (course, len(course_students[course]),
                                  len(course_tas[course])))
        self.get(successes, warnings, errors)

    def reset(self):
        if self.request.get('confirm') != '0xDEADBEEF':
            self.get(errors=['Invalid confirmation'])
        else:
            db.delete([x for x in EvalInvite.all(keys_only=True)])
            db.delete([x for x in Eval.all(keys_only=True)])
            self.get(successes=['Reset Database'])

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


class Dummy(object):
    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            self.__dict__[k] = v


application = webapp.WSGIApplication([('/', HomePage),
                                      (r'/eval/([0-9a-f]+)', EvalPage),
                                      ('/admin', AdminPage),
                                      (r'/admin/all', AdminStatPage),
                                      (r'/admin/([^/]+)', AdminStatPage),
                                      (r'/admin/([^/]+)/([^/]+)',
                                       AdminStatPage),
                                      (r'/.*', ErrorPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
