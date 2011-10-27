import cgi, datetime, logging, math, os, pickle, random, re, tarfile, time
import urllib, StringIO

from google.appengine.dist import use_library
use_library('django', '1.2')

from google.appengine.api import mail, taskqueue, users
from google.appengine.ext import db, webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.runtime import apiproxy_errors, DeadlineExceededError

# catch CapabilityDisabledError on model puts for downtimes
# Test resource http://pastebin.com/bbidrU7g

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')
CD_ATTACHMENT = 'attachement; filename="%s"'

EVAL_TIME = 120 # in hours

COURSE_RE = re.compile('^[a-zA-Z0-9_]+$')
TA_NAME_RE = re.compile('^[a-zA-Z -]+$')
STUDENT_EMAIL_RE = re.compile('^.*@.*$')

MAX_DELETES = 250

EMAIL_SUBJECT = 'Computer Science Midterm TA Evaluations'
EMAIL_TEMPLATE = """Student,

You are receiving this email because you are currently enrolled in the Computer
Science Department courses listed below. Your feedback is incredibly important
as it allows your TA to make the necessary adjustments in order to be of better
help to you and other students. Please submit an evaluation for the appropriate
TA(s) in each course you are enrolled in. For TAs with whom you do not
interact, please select the "Not Applicable" response.

Students who complete 100%% of their evaluations will be automatically entered
into a raffle for a $25 gift card at Amazon. You will receive an additional
email stating your entrance into the raffle when you've completed all the
evaluations.

Please note that the aggregate feedback for each TA will be viewed by that TA,
in addition to the Lead TA (http://cs.ucsb.edu/~leadta) and the course
instructor.

It is important to note that this evaluation system was designed to provide you
with anonymity. The server's database stores the aggregate results for each TA,
as well as a mapping between emails and outstanding evaluations. Upon form
submission, your evaluation is automatically aggregated with the other
evaluations for a particular TA, thus there is no way to associate you to your
submission. Furthermore, the Lead TA is the only person with access to the
server's database. The complete source for the evaluation web app is available
at http://code.google.com/p/taevals/

%s

Thank You,
%s"""

COMPLETED_EMAIL_SUBJECT = 'Computer Science Midterm TA Evaluation Raffle'
COMPLETED_EMAIL_TEMPLATE = """Student,

Thank you for completing all of your midterm TA evaluations. In appreciation of
your efforts, you have been automatically entered into our raffle for a $25
Amazon gift card. The winner will be announced through the CS mailing list
shortly after the evaluation period ends.

Thanks,
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

def nsorted(l):
    convert = lambda text: int(text) if text.isdigit() else text
    key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=key)

class Completed(db.Model):
    email = db.StringProperty(required=True)

class EvalInvite(db.Model):
    email = db.StringProperty(required=True)
    email_sent = db.BooleanProperty(default=False)
    email_from = db.StringProperty()
    email_from_nick = db.StringProperty()
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

    @property
    def url(self):
        return 'https://%s/eval/%s' % (os.environ['HTTP_HOST'],
                                       self.key().name())

    def __lt__(self, other):
        return self.course == nsorted((self.course, other.course))[0]

    def remaining_evals(self):
        query = EvalInvite.all()
        query.filter('__key__ !=', self.key())
        query.filter('email', self.email)
        remaining = []
        for ei in query:
            if len(ei.tas):
                remaining.append(ei)
        return sorted(remaining)


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

        if responded:
            mean = '%.1f' % (sum(tmp) * 1. / responded)
            if len(tmp) % 2 == 0:
                median = '%.1f' % ((tmp[len(tmp) / 2] +
                                    tmp[len(tmp) / 2 - 1]) / 2.)
            else:
                median = '%.1f' % tmp[len(tmp) / 2]
        else:
            mean = median = '-'
        s += '| %4d   %4d   %4s   %4s\n\n' % (values[0], total, mean, median)
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
            remaining = ei.remaining_evals()
            if not remaining:
                if not Completed.all().filter('email', ei.email).fetch(1):
                    body = COMPLETED_EMAIL_TEMPLATE % ei.email_from_nick
                    try:
                        mail.send_mail(ei.email_from, ei.email,
                                       COMPLETED_EMAIL_SUBJECT, body)
                    except apiproxy_errors.OverQuotaError, message:
                        logging.error(message)
                    a = Completed(email=ei.email)
                    a.put()

            expire_time = ei.date + datetime.timedelta(hours=EVAL_TIME)
            ei.expired = datetime.datetime.now() > expire_time

            if not responses:
                responses = [''] * len(QUESTIONS)
            questions = zip(QUESTIONS, responses)

            template_values = {'ei':ei, 'success':success, 'errors':errors,
                               'sel_ta':ta, 'questions':questions,
                               'remaining':remaining}
            path = os.path.join(VIEW_PATH, 'eval.html')
            self.response.out.write(template.render(path, template_values))
        else:
            self.redirect('/')

    def post(self, key):
        ei = EvalInvite.get_by_key_name(key)
        if not ei: return self.redirect('/')
        expire_time = ei.date + datetime.timedelta(hours=EVAL_TIME)
        if datetime.datetime.now() > expire_time:
            return self.get(key)

        ta = self.request.get('ta')
        if ta not in ei.tas:
            return self.get(key, errors=('Must select a TA to evaluate',))

        if self.request.get('not_applicable'):
            success = 'Not Applicable: %s' % ta
        else:
            errors = []
            responses = self.get_responses()

            for i in range(len(QUESTIONS)):
                if i >= len(responses):
                    responses.append('')
                    continue
                if QUESTIONS[i][1] in [0, 1]:
                    if responses[i] not in ['0', '1', '2', '3', '4', '5']:
                        responses[i] = ''
                        errors.append('Must provide an answer for "%s"' %
                                      QUESTIONS[i][0])
            if errors:
                return self.get(key, ta, responses, errors=errors)

            try:
                db.run_in_transaction(Eval.create_or_update, ta, ei.course,
                                      responses)
            except apiproxy_errors.RequestTooLargeError, message:
                return self.get(key, ta, responses,
                                errors=('Your response is too long',))
            success = 'Evaluated: %s' % ta

        # Remove TA from list of TAs student can evaluate
        ei.tas.remove(ta)
        ei.put()
        self.get(key, success=success)

    def get_responses(self):
        args = self.request.arguments()
        resp_args = [int(x[4:]) - 1 for x in args if x.startswith('resp')]

        responses = []
        count = 0
        for resp_num in sorted(resp_args):
            while count < resp_num:
                responses.append('')
                count += 1
            responses.append(self.request.get('resp%d' % (resp_num + 1)))
            count += 1

        return responses


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
            title = '%s-%s' % (course, ta)
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
            cd = CD_ATTACHMENT % '%s.txt' % title.replace(' ', '_')
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.headers['Content-Disposition'] = cd
            self.response.out.write(results)
        else:
            path = os.path.join(VIEW_PATH, 'results.html')
            dl_url = '%s?dl=1' % self.request.url
            template_values = {'results':cgi.escape(results), 'title':title,
                               'dl_url':dl_url}
            self.response.out.write(template.render(path, template_values))


class ResultDownload(webapp.RequestHandler):
    def get(self):
        outfile = StringIO.StringIO()
        tgz = tarfile.open(fileobj=outfile, mode='w:gz')
        
        for ev in Eval.all():
            filename = 'taevals/%s-%s.txt' % (ev.course,
                                              ev.ta.replace(' ', '_'))
            results = Eval.generate_summary([ev]).encode('utf-8')
            tarinfo = tarfile.TarInfo(filename)
            tarinfo.size = len(results)
            tarinfo.mtime = time.time()
            tgz.addfile(tarinfo, StringIO.StringIO(results))
        tgz.close()

        cd = CD_ATTACHMENT % 'taevals.tar.gz'
        self.response.headers['Content-Type'] = 'application/x-compressed'
        self.response.headers['Content-Disposition'] = cd
        self.response.out.write(outfile.getvalue())


class InitInvitesWorker(webapp.RequestHandler):
    def post(self):
        course = self.request.get('course')
        students = self.request.get_all('students')
        tas = self.request.get_all('tas')

        for student in students[:]:
            try:
                EvalInvite.create_new(student, course, tas)
            except DeadlineExceededError, message:
                taskqueue.add(url='/admin/init', params={'course':course,
                                                         'students':students,
                                                         'tas':tas})
                return self.response.set_status(200)
            students.remove(student)


class EmailWorker(webapp.RequestHandler):
    def post(self):
        email_from = self.request.get('from').strip()
        nickname = self.request.get('nickname').strip()
        if email_from == '' or nickname == '':
            return self.response.set_status(400)

        students = {}
        for ei in EvalInvite.all():
            if not ei.tas: continue
            elif ei.email_sent: continue

            if ei.email in students:
                students[ei.email].append(ei)
            else:
                students[ei.email] = [ei]

        for email, eis in students.items():
            output = ''
            for ei in sorted(eis):
                output += '\n'.join(['-%s' % ei.course,
                                     '\tTAs: %s' % ', '.join(sorted(ei.tas)),
                                     '\tURL: %s' % ei.url, ''])
            try:
                mail.send_mail(email_from, email, EMAIL_SUBJECT,
                               EMAIL_TEMPLATE % (output, nickname))
                # Update invite email_sent field
                for ei in eis:
                    ei.email_sent = True
                    ei.email_from = email_from
                    ei.email_from_nick = nickname
                    ei.put()
            except apiproxy_errors.OverQuotaError, message:
                taskqueue.add(url='/admin/email', countdown=60,
                              params={'from':email_from, 'nickname':nickname})
                return self.response.set_status(200)


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

        form_token = hex(random.randint(0, 0xFFFFFFFF))[2:]
        expires = datetime.datetime.now() + datetime.timedelta(minutes=5)
        expires_rfc822 = expires.strftime('%a %d %b %Y %H:%M:%S GMT')
        cookie = "token=%s;expires=%s;path=/" % (form_token, expires_rfc822)

        # Make more user friendly
        if warnings:
            warnings = sorted(warnings)
        if errors:
            errors = sorted(errors)
        courses = [(x, sorted(courses[x].items())) for x in nsorted(courses)]

        template_values = {'successes':successes, 'warnings':warnings,
                           'errors':errors, 'courses':courses,
                           'admin':users.get_current_user(),
                           'form_token':form_token, 'eval_time':EVAL_TIME}
        path = os.path.join(VIEW_PATH, 'admin.html')
        self.response.headers.add_header("Set-Cookie", cookie)
        self.response.out.write(template.render(path, template_values))

    def post(self):
        action = self.request.get('action')
        if not action:
            return self.get()

        if not self.form_token_match():
            return self.get(errors=['Invalid form token.'])

        if action == 'email':
            self.setup_email_invites()
        elif action == 'upload':
            self.upload_csv()
        elif action == 'reset':
            self.reset()
        else:
            self.get(errors=['Invalid action: %s' % action])

    def form_token_match(self):
        return 'token' in self.request.cookies and \
            self.request.cookies['token'] == self.request.get('token')

    def setup_email_invites(self):
        if self.request.get('name') == '':
            return self.get(errors=['From name cannot be blank'])
        else:
            nickname = self.request.get('name')
        user = users.get_current_user()
        email_from = '%s <%s>' % (nickname, user.email())

        taskqueue.add(url='/admin/email', params={'from':email_from,
                                                  'nickname':nickname})
        self.get(['Emails queued'])

        
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
            course_tas, errors = self._process_csv(tas, errors, 'TA',
                                                   TA_NAME_RE)

        if students == '':
            errors.add('No student file submitted.')
        else:
            course_students, errors = self._process_csv(students, errors,
                                                        'student',
                                                        STUDENT_EMAIL_RE)

        s_tas = set(course_tas)
        s_students = set(course_students)
        courses = s_tas.intersection(s_students)

        for course in s_tas - courses:
            warnings.add('Skipping course with no students: %s' % course)
        for course in s_students - courses:
            warnings.add('Skipping course with no tas: %s' % course)

        if len(errors) == 0:
            for course in courses:
                taskqueue.add(url='/admin/init',
                              params={'course':course,
                                      'students':course_students[course],
                                      'tas':course_tas[course]})
            successes.append('Created invite creation task')

        self.get(successes, warnings, errors)

    def reset(self):
        if self.request.get('confirm') != '0xDEADBEEF':
            self.get(errors=['Invalid confirmation'])
        else:
            keys = [x for x in EvalInvite.all(keys_only=True)]
            keys.extend([x for x in Eval.all(keys_only=True)])
            keys.extend([x for x in Completed.all(keys_only=True)])
            for i in range(int(math.ceil(len(keys) * 1. / MAX_DELETES))):
                db.delete(keys[i * MAX_DELETES:(i + 1) * MAX_DELETES])
            self.get(successes=['Reset Database'])

    @staticmethod
    def _process_csv(body, errors, descriptor, validator=None):
        to_return = {}
        for line in [x.strip() for x in body.split('\n') if x != '']:
            split = [x.strip() for x in line.split(',') if x != '']
            if len(split) < 2:
                errors.add('Invalid TA file beginning at: %s' % line)
                break
            course = split[0].lower()
            names = split[1:]
            
            if not COURSE_RE.match(course):
                errors.add('Invalid course name in %s file: %s' %
                           (descriptor, course))
                continue

            if validator:
                for name in names:
                    if not validator.match(name):
                        errors.add('Invalid %s %s' % (descriptor, name))

            if course in to_return:
                errors.add('Duplicate course listing in %s file: %s' %
                           (descriptor, course))
            else:
                to_return[course] = names
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
                                      ('/admin/all', AdminStatPage),
                                      ('/admin/dl', ResultDownload),
                                      ('/admin/email', EmailWorker),
                                      ('/admin/init', InitInvitesWorker),
                                      (r'/admin/s/([^/]+)', AdminStatPage),
                                      (r'/admin/s/([^/]+)/([^/]+)',
                                       AdminStatPage),
                                      (r'/.*', ErrorPage)], debug=True)

def main():
    run_wsgi_app(application)

if __name__ == '__main__':
    main()
