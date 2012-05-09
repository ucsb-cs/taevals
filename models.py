import json
import logging
import os
import random
import textwrap
from google.appengine.ext import db

import const


class Settings(db.Model):
    expire_date = db.DateTimeProperty(required=True)

class Completed(db.Model):
    email = db.StringProperty(required=True)

class EvalInvite(db.Model):
    course = db.StringProperty(required=True)
    email = db.StringProperty(required=True)
    email_sent = db.DateTimeProperty()
    name = db.StringProperty(required=True)
    tas = db.StringListProperty(required=True)

    @staticmethod
    def create(course, student, tas):
        cur = None, None
        while cur != (student['email'], course):
            key_name = hex(random.randint(0, 0xFFFFFFFF))[2:]
            tmp = EvalInvite.get_or_insert(key_name, course=course,
                                           email=student['email'],
                                           name=student['name'], tas=tas)
            cur = tmp.email, tmp.course
        return tmp

    @property
    def url(self):
        return 'https://{}/eval/{}'.format(os.environ['HTTP_HOST'],
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
    course = db.StringProperty(required=True)
    instructor = db.StringProperty(required=True)
    instructor_email = db.StringProperty(required=True)
    responses = db.BlobProperty(required=True)
    sent_results = db.BooleanProperty(default=False)
    ta = db.StringProperty(required=True)
    ta_email = db.StringProperty(required=True)

    def get_responses(self):
        return json.loads(self.responses)

    def update_response_list(self, responses):
        current = json.loads(self.responses)
        for i, (_, q_type) in enumerate(const.QUESTIONS):
            response = responses[i].strip()
            if not response: continue

            if q_type in [0, 1]:
                current[i][int(response)] += 1
            else:
                current[i].append(response)
        self.responses = json.dumps(current)

    @staticmethod
    def create(course, ta, instructor):
        key_name = '{}-{}'.format(ta['name'], course)
        response_list = Eval._construct_response_list()
        obj = Eval(key_name=key_name, course=course,
                   instructor=instructor['name'],
                   instructor_email=instructor['email'],
                   responses=response_list,
                   ta=ta['name'], ta_email=ta['email'])
        obj.put()

    @staticmethod
    def update(course, ta, responses):
        key_name = '{}-{}'.format(ta, course)
        obj = Eval.get_by_key_name(key_name)
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
    def generate_summary(evals, skip=False):
        wrapper = textwrap.TextWrapper(width=79)
        responses = evals[0].get_responses()
        for eval in evals[1:]:
            tmp = eval.get_responses()
            for q_num, (_, q_type) in enumerate(const.QUESTIONS):
                if q_type in [0, 1]:
                    for r_num in range(len(tmp[q_num])):
                        responses[q_num][r_num] += tmp[q_num][r_num]
                else:
                    responses[q_num].extend(tmp[q_num])
        s = ''
        for q_num, (question, q_type) in enumerate(const.QUESTIONS):
            if skip and q_type == 2:
                continue
            wrapper.initial_indent = wrapper.subsequent_indent = ' ' * 8
            s += '    %2d. %s\n' % (q_num+1, wrapper.fill(question)[8:])
            wrapper.initial_indent = wrapper.subsequent_indent = ' ' * 11
            if q_type in [0, 1]:
                s += '        {}\n'.format(const.Q_KEY[q_type])
                s += const.Q_H
                s += Eval.formatted_question_stats(responses[q_num])
            else:
                for i, res in enumerate(sorted(responses[q_num])):
                    tmp = ''
                    for block in res.split('\n'):
                        tmp += '{}\n'.format(wrapper.fill(block))
                    s += '%5s %3d. %s' % (' ', i+1, tmp[11:])
                s += '\n'
        return s

    @staticmethod
    def _construct_response_list():
        responses = []
        for _, q_type in const.QUESTIONS:
            if q_type in [0, 1]:
                responses.append([0, 0, 0, 0, 0, 0])
            else:
                responses.append([])
        return json.dumps(responses)
