import StringIO
import cgi
import datetime
import jinja2
import json
import logging
import math
import os
import tarfile
import time
import urllib
import webapp2
from google.appengine.api import mail, taskqueue, users
from google.appengine.ext import db
from google.appengine.runtime import apiproxy_errors, DeadlineExceededError

import const
import helpers
from models import Completed, Eval, EvalInvite, Settings

jinja_environment = jinja2.Environment(loader=jinja2.FileSystemLoader(
        os.path.join(os.path.dirname(__file__), 'views')))

# catch CapabilityDisabledError on model puts for downtimes
# Test resource http://pastebin.com/bbidrU7g

VIEW_PATH = os.path.join(os.path.dirname(__file__), 'views')
MAX_DELETES = 250


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
                    courses[invite.course][ta] = helpers.Dummy(
                        remaining=1, completed=0, sent_results=None)

        # Completed Evaluations
        for evaluation in Eval.all():
            completed = sum(evaluation.get_responses()[0])
            tas = courses[evaluation.course]
            if evaluation.ta in tas:
                tas[evaluation.ta].completed = completed
                tas[evaluation.ta].sent_results = evaluation.sent_results
            else:
                tas[evaluation.ta] = helpers.Dummy(
                    completed=completed, remaining=0,
                    sent_results=evaluation.sent_results)

        form_token, cookie = helpers.generate_validation_token()
        self.response.headers.add_header('Set-Cookie', cookie)

        successes = helpers.nsorted(successes) if successes else []
        warnings = helpers.nsorted(warnings) if warnings else []
        errors = helpers.nsorted(errors) if errors else []
        courses = [(x, sorted(courses[x].items())) for x in
                   helpers.nsorted(courses)]

        # Initialize settings if not already set
        user = users.get_current_user()
        admin_email = 'Computer Science Lead TA <{}>'.format(user.email())
        now = datetime.datetime.now()
        expire_date = now + datetime.timedelta(days=5)
        settings = Settings.get_or_insert('settings', admin_email=admin_email,
                                          expire_date=expire_date)
        if settings.expire_date < now:
            remaining_time = str(datetime.timedelta())
        else:
            remaining_time = str(settings.expire_date - now)

        values = {'successes': successes, 'warnings': warnings,
                  'errors': errors, 'courses': courses,
                  'form_token': form_token, 'eval_time': remaining_time}
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
        elif action == 'email_json':
            self.download_email_json()
        elif action == 'email_result':
            self.email_result()
        elif action == 'expire_date':
            self.update_expire_date()
        elif action == 'reset':
            self.reset()
        elif action == 'upload':
            self.upload_json()
        else:
            self.get(errors=['Invalid action: {}'.format(action)])

    def download_email_json(self):
        data = {'template': const.EMAIL_TEMPLATE, 'emails': []}
        now = datetime.datetime.now()
        for invites, output in helpers.invite_iterator():
            name = invites[0].name
            email = '{} <{}>'.format(name, invites[0].email)
            cur = {'name': invites[0].name, 'email': email, 'output': output}
            data['emails'].append(cur)
            for invite in invites:
                invite.email_sent = now
                invite.put()
        if data['emails']:
            cd = 'attachement; filename="emails.json"'
            self.response.headers['Content-Type'] = 'text/plain'
            self.response.headers['Content-Disposition'] = cd
            self.response.out.write(json.dumps(data))
        else:
            self.get(warnings=('No more emails to send.',))

    def email_result(self):
        key_name = self.request.get('key')
        obj = Eval.get_by_key_name(key_name)
        if not obj:
            return self.get(errors=['Invalid key: {!r}'.format(key_name)])

        if obj.sent_results:
            return self.get(errors=['Results already emailed.'])

        safe_ta_name = obj.ta.replace(' ', '_')
        ta_result_name = '{}-{}.txt'.format(obj.course, safe_ta_name)
        ta_result = Eval.generate_summary([obj])

        course_list = [x for x in Eval.all().filter('course =', obj.course)]
        course_result = Eval.generate_summary(course_list, True)
        all_result = Eval.generate_summary([x for x in Eval.all()], True)

        settings = Settings.get_by_key_name('settings')
        email_to = '{} <{}>'.format(obj.ta, obj.ta_email)
        email_cc = '{} <{}>'.format(obj.instructor, obj.instructor_email)
        body = const.RESULT_EMAIL_TEMPLATE.format(obj.ta)
        attachments = [(ta_result_name, ta_result),
                       ('{}.txt'.format(obj.course), course_result),
                       ('all.txt', all_result)]

        try:
            mail.send_mail(sender=settings.admin_email, to=email_to,
                           cc=email_cc, subject=const.RESULT_EMAIL_SUBJECT,
                           body=body, attachments=attachments)
            obj.sent_results = True
            obj.put()
            self.get(['Sent result to {} and {}'.format(obj.ta,
                                                        obj.instructor)])
        except Exception, e:
            self.get(errors=[str(e)])

    def form_token_match(self):
        return ('token' in self.request.cookies and
                self.request.cookies['token'] == self.request.get('token'))

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

    def setup_email_invites(self):
        taskqueue.add(url='/admin/email')
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


class AdminStatPage(webapp2.RequestHandler):
    def get(self, course=None, ta=None):
        evals = []
        key_name = None
        if ta != None:
            ta = urllib.unquote(ta)
            course = urllib.unquote(course)
            if course == None:
                return self.redirect('/admin')
            key_name = '{}-{}'.format(ta, course)
            obj = Eval.get_by_key_name(key_name)
            if obj == None:
                return self.redirect('/admin')
            evals.append(obj)
            title = '{}-{}'.format(course, ta)
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
            form_token, cookie = helpers.generate_validation_token()
            self.response.headers.add_header('Set-Cookie', cookie)

            dl_url = '{}?dl=1'.format(self.request.url)
            email_url = '{}?email=1'.format(self.request.url)
            values = {'results': cgi.escape(results), 'title': title,
                      'dl_url': dl_url, 'email_url': email_url,
                      'form_token': form_token, 'key': key_name}
            template = jinja_environment.get_template('results.html')
            self.response.out.write(template.render(values))


class ErrorPage(webapp2.RequestHandler):
    def get(self):
        self.redirect('/', permanent=True)


class EvalPage(webapp2.RequestHandler):
    def get(self, key, ta='', responses=None, success=None, errors=None):
        invite = EvalInvite.get_by_key_name(key)
        if not invite:
            return self.redirect('/')

        settings = Settings.get_by_key_name('settings')
        invite.expired = datetime.datetime.now() > settings.expire_date
        success = success or []
        errors = errors or []

        remaining = invite.remaining_evals()
        if not remaining and not invite.tas:
            if settings.send_completed_email:
                body = const.COMPLETED_EMAIL_TEMPLATE.format(invite.name)
                try:
                    to_email = '{} <{}>'.format(invite.name, invite.email)
                    mail.send_mail(settings.admin_email, to_email,
                                   const.COMPLETED_EMAIL_SUBJECT, body)
                except apiproxy_errors.OverQuotaError as message:
                    logging.error(message)
            completed = Completed(name=invite.name, email=invite.email)
            completed.put()
            invite.delete()
            questions = None
        else:
            if not responses:
                responses = [''] * len(const.QUESTIONS)
            questions = zip(const.QUESTIONS, responses)

        values = {'invite': invite, 'success': success, 'errors': errors,
                  'sel_ta': ta, 'questions': questions, 'remaining': remaining}
        template = jinja_environment.get_template('eval.html')
        self.response.out.write(template.render(values))

    def post(self, key):
        invite = EvalInvite.get_by_key_name(key)
        if not invite:
            return self.redirect('/')

        settings = Settings.get_by_key_name('settings')
        if datetime.datetime.now() > settings.expire_date:
            return self.get(key)

        ta = self.request.get('ta')
        if ta not in invite.tas:
            return self.get(key, errors=('Must select a TA to evaluate',))

        if self.request.get('not_applicable'):
            success = 'Not Applicable: {}'.format(ta)
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
                        errors.append('Must provide an answer for {!r}'.format(
                                const.QUESTIONS[i][0]))
            if errors:
                return self.get(key, ta, responses, errors=errors)

            try:
                db.run_in_transaction(Eval.update, invite.course, ta,
                                      responses)
            except apiproxy_errors.RequestTooLargeError:
                return self.get(key, ta, responses,
                                errors=('Your response is too long',))
            success = 'Evaluated: {}'.format(ta)

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
            responses.append(self.request.get('resp{}'.format((resp_num + 1))))
            count += 1
        return responses


class HomePage(webapp2.RequestHandler):
    def get(self):
        template = jinja_environment.get_template('home.html')
        self.response.out.write(template.render())


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


class EmailWorker(webapp2.RequestHandler):
    def post(self):
        settings = Settings.get_by_key_name('settings')
        for invites, output in helpers.invite_iterator():
            name = invites[0].name
            to_email = '{} <{}>'.format(name, invites[0].email)
            body = const.EMAIL_TEMPLATE.format(student=name, body=output)
            try:
                mail.send_mail(settings.admin_email, to_email,
                               const.EMAIL_SUBJECT, body)
                now = datetime.datetime.now()
                for invite in invites:
                    invite.email_sent = now
                    invite.put()
            except apiproxy_errors.OverQuotaError:
                taskqueue.add(url='/admin/email', countdown=60)
                return self.response.set_status(200)


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


app = webapp2.WSGIApplication([('/', HomePage),
                               (r'/eval/([0-9a-f]+)', EvalPage),
                               ('/admin', AdminPage),
                               ('/admin/all', AdminStatPage),
                               ('/admin/dl', ResultDownload),
                               ('/admin/email', EmailWorker),
                               ('/admin/init', InitWorker),
                               (r'/admin/s/([^/]+)', AdminStatPage),
                               (r'/admin/s/([^/]+)/([^/]+)', AdminStatPage),
                               (r'/.*', ErrorPage)], debug=False)
