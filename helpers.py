import datetime
import random
import re


class Dummy(object):
    def __init__(self, *args, **kwargs):
        for k, v in kwargs.items():
            self.__dict__[k] = v


def nsorted(l):
    convert = lambda text: int(text) if text.isdigit() else text
    key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
    return sorted(l, key=key)


def generate_validation_token():
    form_token = hex(random.randint(0, 0xFFFFFFFF))[2:]
    expires = datetime.datetime.now() + datetime.timedelta(minutes=5)
    expires_rfc822 = expires.strftime('%a %d %b %Y %H:%M:%S GMT')
    cookie = 'token={};expires={};path=/'.format(form_token, expires_rfc822)
    return form_token, cookie
