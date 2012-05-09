import StringIO
import cgi
import datetime
import jinja2
import json
import logging
import math
import os
import random
import re
import tarfile
import time
import urllib
import webapp2
from google.appengine.api import mail, taskqueue, users
from google.appengine.ext import db
from google.appengine.runtime import apiproxy_errors, DeadlineExceededError

import const
from models import Completed, Eval, EvalInvite, Settings

jinja_environment = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.join(os.path.dirname(__file__), 'views')))

# catch CapabilityDisabledError on model puts for downtimes
# Test resource http://pastebin.com/bbidrU7g

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')
MAX_DELETES = 250

def nsorted(l):
    convert = lambda text: int(text) if text.isdigit() else text
    key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=key)

def generate_validation_token():
    form_token = hex(random.randint(0, 0xFFFFFFFF))[2:]
    expires = datetime.datetime.now() + datetime.timedelta(minutes=5)
    expires_rfc822 = expires.strftime('%a %d %b %Y %H:%M:%S GMT')
    cookie = "token=%s;expires=%s;path=/" % (form_token, expires_rfc822)
    return form_token, cookie


class HomePage(webapp2.RequestHandler):
    def get(self):
        template = jinja_environment.get_template('home.html')
        self.response.out.write(template.render())


class EvalPage(webapp2.RequestHandler):
    def get(self, key, ta='', responses=None, success=None, errors=None):
        invite = EvalInvite.get_by_key_name(key)
        if invite:
            remaining = invite.remaining_evals()
            if not remaining and not invite.tas:
                if not Completed.all().filter('email', invite.email).fetch(1):
                    body = const.COMPLETED_EMAIL_TEMPLATE.format(
                        invite.email_from_nick)
                    try:
                        mail.send_mail(invite.email_from, invite.email,
                                       const.COMPLETED_EMAIL_SUBJECT, body)
                    except apiproxy_errors.OverQuotaError, message:
                        logging.error(message)
                    a = Completed(email=invite.email)
                    a.put()

            settings = Settings.get_by_key_name('settings')
            invite.expired = datetime.datetime.now() > settings.expire_date

            if not responses:
                responses = [''] * len(const.QUESTIONS)
            questions = zip(const.QUESTIONS, responses)


            success = success or []
            errors = errors or []

            values = {'invite':invite, 'success':success, 'errors':errors,
                      'sel_ta':ta, 'questions':questions, 'remaining':remaining}
            template = jinja_environment.get_template('eval.html')
            self.response.out.write(template.render(values))
        else:
            self.redirect('/')

    def post(self, key):
        invite = EvalInvite.get_by_key_name(key)
        if not invite: return self.redirect('/')

        settings = Settings.get_by_key_name('settings')
        if datetime.datetime.now() > settings.expire_date:
            return self.get(key)

        ta = self.request.get('ta')
        if ta not in invite.tas:
            return self.get(key, errors=('Must select a TA to evaluate',))

        if self.request.get('not_applicable'):
            success = 'Not Applicable: %s' % ta
        else:
            errors = []
            responses = self.get_responses()

            for i in range(len(const.QUESTIONS)):
                if i >= len(responses):
                    responses.append('')
                    continue
                if const.QUESTIONS[i][1] in [0, 1]:
                    if responses[i] not in ['0', '1', '2', '3', '4', '5']:
                        responses[i] = ''
                        errors.append('Must provide an answer for "%s"' %
                                      const.QUESTIONS[i][0])
            if errors:
                return self.get(key, ta, responses, errors=errors)

            try:
                db.run_in_transaction(Eval.update, invite.course, ta, responses)
            except apiproxy_errors.RequestTooLargeError, message:
                return self.get(key, ta, responses,
                                errors=('Your response is too long',))
            success = 'Evaluated: %s' % ta

        # Remove TA from list of TAs student can evaluate
        invite.tas.remove(ta)
        invite.put()
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


class AdminStatPage(webapp2.RequestHandler):
    def get(self, course=None, ta=None):
        evals = []
        key_name = None
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

        results = Eval.generate_summary(evals, len(evals) > 1)

        if self.request.get('dl') == '1':
            filename = title.replace(' ', '_')
            cd = 'attachement; filename="{}.txt"'.format(filename)
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.headers['Content-Disposition'] = cd
            self.response.out.write(results)
        else:
            form_token, cookie = generate_validation_token()
            self.response.headers.add_header("Set-Cookie", cookie)

            dl_url = '{}?dl=1'.format(self.request.url)
            email_url = '{}?email=1'.format(self.request.url)
            values = {'results':cgi.escape(results), 'title':title,
                      'dl_url':dl_url, 'email_url':email_url,
                      'form_token':form_token, 'key':key_name}
            template = jinja_environment.get_template('results.html')
            self.response.out.write(template.render(values))


class ResultDownload(webapp2.RequestHandler):
    def get(self):
        outfile = StringIO.StringIO()
        tgz = tarfile.open(fileobj=outfile, mode='w:gz')

        for ev in Eval.all():
            filename = 'taevals/{}-{}.txt'.format(ev.course,
                                                  ev.ta.replace(' ', '_'))
            results = Eval.generate_summary([ev]).encode('utf-8')
            tarinfo = tarfile.TarInfo(filename)
            tarinfo.size = len(results)
            tarinfo.mtime = time.time()
            tgz.addfile(tarinfo, StringIO.StringIO(results))
        tgz.close()

        cd = 'attachement; filename="taevals.tar.gz"'
        self.response.headers['Content-Type'] = 'application/x-compressed'
        self.response.headers['Content-Disposition'] = cd
        self.response.out.write(outfile.getvalue())


class InitWorker(webapp2.RequestHandler):
    def post(self):
        course = self.request.get('course')
        try:
            data = json.loads(self.request.get('data'))
        except ValueError:
            data = None
        if not course or not data:
            return self.response.set_status(202)

        # Create Evals
        if data['instructor']:
            for ta in data['tas']:
                Eval.create(course, ta, data['instructor'])
            data['instructor'] = None

        # Create Invites
        tas = [ta['name'] for ta in data['tas']]
        for student in data['students'][:]:
            try:
                EvalInvite.create(course, student, tas)
            except DeadlineExceededError:
                # Need to test this
                data = json.dumps(data)
                taskqueue.add(url='/admin/init', params={'course': course,
                                                         'data': data})
                return self.response.set_status(200)
            data['students'].remove(student)
        return self.response.set_status(201)


class EmailWorker(webapp2.RequestHandler):
    def post(self):
        email_from = self.request.get('from').strip()
        nickname = self.request.get('nickname').strip()
        if email_from == '' or nickname == '':
            return self.response.set_status(400)

        students = {}
        for invite in EvalInvite.all():
            if not invite.tas or invite.email_sent:
                continue
            students.setdefault(invite.email, []).append(invite)

        output_tmpl = '-{}\n\tTAs: {}\n\tURL: {}\n'
        for email, invites in students.items():
            output = ''
            for invite in sorted(invites):
                output += output_tmpl.format(invite.course,
                                             ', '.join(sorted(invite.tas)),
                                             invite.url)
            try:
                mail.send_mail(email_from, email, const.EMAIL_SUBJECT,
                               const.EMAIL_TEMPLATE.format(student=invite.name,
                                                           body=output,
                                                           sender=nickname))
                for invite in invites:
                    invite.email_sent = datetime.datetime.now()
                    invite.put()
            except apiproxy_errors.OverQuotaError, message:
                taskqueue.add(url='/admin/email', countdown=60,
                              params={'from': email_from, 'nickname': nickname})
                return self.response.set_status(200)
            break


class AdminPage(webapp2.RequestHandler):
    def get(self, successes=None, warnings=None, errors=None):
        courses = {}

        # Remaining Evaluations
        for invite in EvalInvite.all():
            if invite.course not in courses:
                courses[invite.course] = {}

            for ta in invite.tas:
                if ta in courses[invite.course]:
                    courses[invite.course][ta].remaining += 1
                else:
                    courses[invite.course][ta] = Dummy(remaining=1, completed=0,
                                                   sent_results=None)

        # Completed Evaluations
        for e in Eval.all():
            completed = sum(e.get_responses()[0])
            if e.ta in courses[e.course]:
                courses[e.course][e.ta].completed = completed
                courses[e.course][e.ta].sent_results = e.sent_results
            else:
                courses[e.course][e.ta] = Dummy(completed=completed,
                                                remaining=0,
                                                sent_results=e.sent_results)

        form_token, cookie = generate_validation_token()
        self.response.headers.add_header("Set-Cookie", cookie)

        successes = nsorted(successes) if successes else []
        warnings = nsorted(warnings) if warnings else []
        errors = nsorted(errors) if errors else []
        courses = [(x, sorted(courses[x].items())) for x in nsorted(courses)]

        now = datetime.datetime.now()
        settings = Settings.get_or_insert('settings', expire_date=now)
        if settings.expire_date < now:
            remaining_time = str(datetime.timedelta())
        else:
            remaining_time = str(settings.expire_date - now)

        values = {'successes':successes, 'warnings':warnings, 'errors':errors,
                  'courses':courses, 'admin':users.get_current_user(),
                  'form_token':form_token, 'eval_time':remaining_time}
        template = jinja_environment.get_template('admin.html')
        self.response.out.write(template.render(values))

    def post(self):
        action = self.request.get('action')
        if not action:
            return self.get()

        if not self.form_token_match():
            return self.get(errors=['Invalid form token.'])

        if action == 'email':
            self.setup_email_invites()
        elif action == 'email_result':
            self.email_result()
        elif action == 'expire_date':
            self.update_expire_date()
        elif action == 'reset':
            self.reset()
        elif action == 'upload':
            self.upload_json()
        else:
            self.get(errors=['Invalid action: %s' % action])

    def form_token_match(self):
        return ('token' in self.request.cookies and
                self.request.cookies['token'] == self.request.get('token'))

    def email_result(self):
        key_name = self.request.get('key')
        obj = Eval.get_by_key_name(key_name)
        if not obj:
            return self.get(errors=['Invalid key: "%s"' % key_name])

        if obj.sent_results:
            return self.get(errors=['Results already emailed.'])

        ta_result_name = '%s-%s.txt' % (obj.course, obj.ta.replace(' ', '_'))
        ta_result = Eval.generate_summary([obj])

        course_list = [x for x in Eval.all().filter('course =', obj.course)]
        course_result = Eval.generate_summary(course_list, True)
        all_result = Eval.generate_summary([x for x in Eval.all()], True)

        user = users.get_current_user()
        email_from = '%s <%s>' % (user.nickname(), user.email())
        email_to = '%s <%s>' % (obj.ta, obj.ta_email)
        email_cc = obj.prof_email
        body = const.RESULT_EMAIL_TEMPLATE % (obj.ta, user.nickname())
        attachments = [(ta_result_name, ta_result),
                       ('%s.txt' % obj.course, course_result),
                       ('all.txt', all_result)]

        try:
            mail.send_mail(sender=email_from,
                           subject=const.RESULT_EMAIL_SUBJECT,
                           to=email_to, cc=email_cc, body=body,
                           attachments=attachments)
            obj.sent_results = True
            obj.put()
            self.get(['Sent result to %s and %s' % (email_to, email_cc)])
        except Exception, e:
            self.get(errors=[str(e)])

    def setup_email_invites(self):
        nickname = self.request.get('name')
        if not nickname:
            return self.get(errors=['Form name cannot be blank'])

        user = users.get_current_user()
        email_from = '%s <%s>' % (nickname, user.email())

        taskqueue.add(url='/admin/email', params={'from':email_from,
                                                  'nickname':nickname})
        self.get(['Emails queued'])

    def update_expire_date(self):
        expire_date = self.request.get('expire_date')
        try:
            expire_date = datetime.datetime.strptime(expire_date,
                                                     '%Y:%m:%d %H:%M')
        except ValueError:
            expire_date = None
        if not expire_date:
            return self.get(errors=['Invalid expire date'])
        settings = Settings.get_by_key_name('settings')
        settings.expire_date = expire_date
        settings.put()
        return self.get(['Expire date updated.'])

    def upload_json(self):
        course_lists = self.request.get('course_lists')

        errors = set()
        warnings = set()
        successes = []

        try:
            course_data = json.loads(course_lists)
        except ValueError:
            errors.add('Invalid json file.')
            course_data = {}

        expected_keys = set(('instructor', 'students', 'tas'))
        person_keys = set(('name', 'email'))

        for course, data in course_data.iteritems():
            missing = expected_keys - set(data.keys())
            if missing:
                warnings.add('{} is missing {!r}.'.format(course,
                                                          ', '.join(missing)))
            elif not data['students']:
                warnings.add('{} has no students.'.format(course))
            elif not data['tas']:
                warnings.add('{} has no TAs.'.format(course))
            elif set(data['instructor'].keys()) != person_keys:
                warnings.add('{} has an invalid instructor.'.format(course))
            elif any(set(x.keys()) != person_keys for x in data['students']):
                warnings.add('{} has an invalid student.'.format(course))
            elif any(set(x.keys()) != person_keys for x in data['tas']):
                warnings.add('{} has an invalid TA.'.format(course))
            else:
                data = json.dumps(data)
                taskqueue.add(url='/admin/init', params={'course': course,
                                                         'data': data})
                successes.append('Adding course {}'.format(course))
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

class ErrorPage(webapp2.RequestHandler):
    def get(self):
        self.redirect('/', permanent=True)


class Dummy(object):
    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            self.__dict__[k] = v


app = webapp2.WSGIApplication([('/', HomePage),
                               (r'/eval/([0-9a-f]+)', EvalPage),
                               ('/admin', AdminPage),
                               ('/admin/all', AdminStatPage),
                               ('/admin/dl', ResultDownload),
                               ('/admin/email', EmailWorker),
                               ('/admin/init', InitWorker),
                               (r'/admin/s/([^/]+)', AdminStatPage),
                               (r'/admin/s/([^/]+)/([^/]+)', AdminStatPage),
                               (r'/.*', ErrorPage)], debug=True)
