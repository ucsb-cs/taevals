import datetime
import random
import re

import models


class Dummy(object):
    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            self.__dict__[k] = v


def generate_validation_token():
    form_token = hex(random.randint(0, 0xFFFFFFFF))[2:]
    expires = datetime.datetime.now() + datetime.timedelta(minutes=5)
    expires_rfc822 = expires.strftime('%a %d %b %Y %H:%M:%S GMT')
    cookie = 'token={};expires={};path=/'.format(form_token, expires_rfc822)
    return form_token, cookie


def invite_iterator(limit=100):
    students = {}
    for invite in models.EvalInvite.all():
        if invite.tas and not invite.email_sent:
            students.setdefault(invite.email, []).append(invite)

    output_tmpl = '-{}\n\tTAs: {}\n\tURL: {}'
    for invites in students.values()[:limit]:
        output = []
        for invite in sorted(invites):
            tas = ', '.join(sorted(invite.tas))
            output.append(output_tmpl.format(invite.course, tas, invite.url))
        yield invites, '\n'.join(output)


def nsorted(l):
    convert = lambda text: int(text) if text.isdigit() else text
    key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=key)
