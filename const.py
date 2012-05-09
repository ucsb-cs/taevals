EMAIL_SUBJECT = 'Computer Science Midterm TA Evaluations'
EMAIL_TEMPLATE = """{student},

You are receiving this email because you are currently enrolled in the
following Computer Science Department courses:

{body}

Your feedback is incredibly important as it allows your TA to make the
necessary adjustments in order to be of better help to you and other
students. Please submit an evaluation for the appropriate TA(s) in each course
you are enrolled in. For TAs with whom you do not interact, please select the
"Not Applicable" response.

Please note that the aggregate feedback for each TA will be viewed by that TA,
in addition to the Lead TA (http://cs.ucsb.edu/~leadta) and the course
instructor.

It is important to note that this evaluation system was designed to provide you
with anonymity. The server's database stores the aggregate results for each TA,
as well as a mapping between students and uncompleted evaluations. Upon form
submission, your evaluation is automatically aggregated with the other
evaluations for a particular TA, thus there is no way to associate you with
your submission. Furthermore, the Lead TA is the only person with access to the
server's database. The complete source for the evaluation web app is available
at https://github.com/ucsb-cs/taevals

Thank You,
Computer Science Lead TA"""

RESULT_EMAIL_SUBJECT = 'CS Midterm TA Evaluation Results'
RESULT_EMAIL_TEMPLATE = """{},

Attached are your individual TA evaluation results, along with the aggregrate
results across all TAs of the same course this quarter, and the aggregate
results for all CS department TAs this quarter. The instructor of the course
has been copied on this email.

If you have any questions please do not hesitate to ask.

Thanks,
Computer Science Lead TA"""

COMPLETED_EMAIL_SUBJECT = 'Computer Science Midterm TA Evaluation Completion'
COMPLETED_EMAIL_TEMPLATE = """{},

Thank you for completing all of your midterm TA evaluations. Enjoy your karma!

Thanks,
Computer Science Lead TA"""

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
