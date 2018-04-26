from uuid import uuid4
from itertools import starmap
import datetime

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse
from django.utils.safestring import mark_safe

from .utils import enroll_generator
from ct.models import (
    CourseUnit,
    UnitLesson,
    Response,
    Unit,
    NEED_REVIEW_STATUS,
    Lesson,
    STATUS_CHOICES,
    StudentError
)
from ct.templatetags.ct_extras import md2html


TYPE_CHOICES = (
    ('text', 'text'),
    ('options', 'options'),
    ('custom', 'custom'),
)

MODEL_CHOISES = (
    ('NoneType', 'NoneType'),
    ('chatdivider', 'chatdivider'),
    ('response', 'response'),
    ('unitlesson', 'unitlesson'),
    ('uniterror', 'uniterror'),
)

MESSAGE_TYPES = (
    ('message', 'message'),
    ('user', 'user'),
    ('breakpoint', 'breakpoint'),
)

KIND_CHOICES = (
    (Lesson.BASE_EXPLANATION, Lesson.BASE_EXPLANATION),
    (Lesson.ORCT_QUESTION, Lesson.ORCT_QUESTION),
    (Lesson.ANSWER, Lesson.ANSWER),
    (Lesson.ERROR_MODEL, Lesson.ERROR_MODEL),
    ('chatdivider', 'chatdivider'),
    ('uniterror', 'uniterror'),
    ('response', 'response'),
    ('message', 'message'),
    ('button', 'button')
)

EVAL_OPTIONS = {
    'close': 'It was very close',
    'different': 'No similarities at all',
    'correct': 'Essentially the same'
}

STATUS_OPTIONS = {
    'help': 'Still confused, need help',
    'review': 'OK, but need further review and practice',
    'done': 'Solidly',
}


class Chat(models.Model):
    """
    Chat model that handles particular student chat.
    """
    next_point = models.OneToOneField('Message', null=True, related_name='base_chat')
    user = models.ForeignKey(User)
    is_open = models.BooleanField(default=False)
    is_live = models.BooleanField(default=False)
    enroll_code = models.ForeignKey('EnrollUnitCode', null=True)
    state = models.OneToOneField('fsm.FSMState', null=True, on_delete=models.SET_NULL)
    instructor = models.ForeignKey(User, blank=True, null=True, related_name='course_instructor')
    last_modify_timestamp = models.DateTimeField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    progress = models.IntegerField(default=0, blank=False, null=False)

    class Meta:
        ordering = ['-last_modify_timestamp']

    def get_options(self):
        options = None
        if self.next_point.input_type == 'options':
            options = self.next_point.get_options()
        return options

    def get_course_unit(self):
        '''
        Return CourseUnit or None if CourseUnit was not found
        :return: CourseUnit instance or None
        '''
        if self.state:
            data = self.state.get_all_state_data()
            if 'course' in data and 'unit' in data:
                return CourseUnit.objects.filter(unit=data['unit'], course=data['course']).first()
        else:
            return self.enroll_code.courseUnit

    def get_spent_time(self):
        '''
        Return timestamp - start_timestamp.
        It's actual time spent for live session.
        :return: datetime.timedelta
        '''
        if not self.last_modify_timestamp:
            last_msg = self.message_set.filter(timestamp__isnull=False).order_by('-timestamp').first()
            if last_msg:
                self.last_modify_timestamp = last_msg.timestamp
                self.save()
            else:
                return datetime.timedelta(0)
        return self.last_modify_timestamp - self.timestamp

    def get_formatted_time_spent(self):
        spent_time = self.get_spent_time()
        return str(spent_time).split('.', 2)[0].replace('-', '')


class Message(models.Model):
    """
    Message model represent chat message.
    """
    chat = models.ForeignKey(Chat, null=True, on_delete=models.SET_NULL)
    text = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(null=True)
    contenttype = models.CharField(
        max_length=16, choices=MODEL_CHOISES, null=True, default='NoneType'
    )
    content_id = models.IntegerField(null=True)
    input_type = models.CharField(max_length=16, choices=TYPE_CHOICES, null=True, default='options')
    lesson_to_answer = models.ForeignKey(UnitLesson, null=True)
    response_to_check = models.ForeignKey(Response, null=True)
    student_error = models.ForeignKey(StudentError, null=True)
    type = models.CharField(max_length=16, default='message', choices=MESSAGE_TYPES)
    owner = models.ForeignKey(User, null=True)
    is_additional = models.BooleanField(default=False)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, null=True)
    userMessage = models.BooleanField(default=False)

    class Meta:
        ordering = ['timestamp']

    @property
    def content(self):
        if self.contenttype == 'NoneType':
            return self.text
        app_label = 'chat' if self.contenttype == 'uniterror' or self.contenttype == 'chatdivider' else 'ct'
        model = ContentType.objects.get(app_label=app_label, model=self.contenttype).model_class()
        if model:
            return model.objects.filter(id=self.content_id).first()
        else:
            return self.contenttype

    def get_next_point(self):
        return self.chat.next_point.id if self.chat and self.chat.next_point else None

    def get_next_input_type(self):
        if self.chat:
            if self.chat.next_point:
                input_type = self.chat.next_point.input_type
            else:
                input_type = 'custom'
        else:
            input_type = None
        return input_type

    def get_next_url(self):
        return reverse(
            'chat:messages-detail',
            args=(self.chat.next_point.id,)
        ) if self.chat and self.chat.next_point else None

    def get_errors(self):
        errors = None
        error_list = UnitError.objects.get(id=self.content_id).get_errors()
        if error_list:
            checked_errors = UnitError.objects.get(
                id=self.content_id
            ).response.studenterror_set.all().values_list('errorModel', flat=True)
            error_str = (
                u'<li><div class="chat-check chat-selectable {}" data-selectable-attribute="errorModel" '
                u'data-selectable-value="{:d}"></div><h3>{}</h3></li>'
            )
            errors = reduce(
                lambda x, y: x+y, map(
                    lambda x: error_str.format(
                        'chat-selectable-selected' if x.id in checked_errors else '',
                        x.id,
                        x.lesson.title
                    ),
                    error_list
                )
            )
        return u'<ul class="chat-select-list">{}</ul>'.format(
            errors or '<li><h3>There are no Misunderstands to display.</h3></li>'
        )

    def render_choices(self, choices, checked_choices):
        """ This method renders choices as like ErrorModels.
        :param choices: choices to render
        :param checked_choices: choices to mark as checked
        """
        choices_template = (
            '<li><div class="chat-check chat-selectable %s" data-selectable-attribute="choices" '
            'data-selectable-value="%d"></div><h3>%s</h3></li>'
        )
        return '<ul class="chat-select-list">' + reduce(
            lambda x, y: x+y,
            starmap(lambda i, x: choices_template % (
                'chat-selectable-selected' if i in checked_choices else '',
                i,
                x[2:] if x.startswith(Lesson.NOT_CORRECT_CHOICE) else x[3:]),
                choices
            )
        ) + '</ul>'

    def render_my_choices(self):
        """ Render user's answer choices.
        """
        if '[selected_choices]' in self.content.text:
            selected = [int(i) for i in self.content.text.split('[selected_choices] ')[1].split()]
            my_choices = []
            for i, c in self.content.lesson.get_choices():
                if i in selected:
                    my_choices.append((i, c))
            return self.render_choices(my_choices, selected)
        else:
            return self.render_choices([], [])

    def get_choices(self):
        """Use this method to return QUESTION."""
        checked_choices = []
        return self.render_choices(self.content.lesson.get_choices(), checked_choices)

    def get_correct_choices(self):
        """Use this method to return ANSWER."""
        checked_choices = []
        return self.render_choices(self.lesson_to_answer.lesson.get_correct_choices(), checked_choices)

    def should_ask_confidence(self):
        """Use this method to check whether this FSM should ask for CONFIDENCE."""
        if self.chat.state:
            return 'CONFIDENCE' in [
                i['name']
                for i in self.chat.state.fsmNode.fsm.fsmnode_set.all().values('name')
            ]
        return False

    def get_options(self):
        options = None
        next_point = self.chat.next_point
        CONTINUE_BTN = {"value": 1, "text": "Continue"}
        if (
            self.chat and next_point and
            next_point.input_type == 'options'
        ):
            if next_point.kind == 'button':
                options = [CONTINUE_BTN]
            elif (next_point.contenttype == 'unitlesson' and
                  self.chat.next_point.content.lesson.sub_kind != 'choices'):
                options = [dict(value=i[0], text=i[1]) for i in STATUS_CHOICES]
            elif (next_point.contenttype == 'response'
                  and next_point.lesson_to_answer
                  and next_point.lesson_to_answer.sub_kind == 'choices'
                  and not self.response_to_check
            ):
                options = [CONTINUE_BTN]
            elif self.chat.next_point.contenttype == 'response':
                if self.should_ask_confidence():
                    if not self.chat.next_point.content.confidence:
                        options = [dict(value=i[0], text=i[1]) for i in Response.CONF_CHOICES]
                    else:
                        options = [dict(value=i[0], text=i[1]) for i in Response.EVAL_CHOICES]
                else:
                    options = [dict(value=i[0], text=i[1]) for i in Response.EVAL_CHOICES]
            else:
                options = [CONTINUE_BTN]
        return options

    def get_html(self):
        html = self.text
        if self.content_id:
            if self.contenttype == 'chatdivider':
                html = self.content.text
            elif self.contenttype == 'response':
                sub_kind = self.content.sub_kind
                if sub_kind and not self.content.selfeval and not self.content.confidence:
                    if sub_kind == 'choices':
                        html = self.render_my_choices()
                        return html

                if sub_kind and self.content.confidence and self.content.selfeval and not self.text:
                    # if look history - we already have confidence and selfeval so just return msg text
                    if self.input_type == 'options':
                        html = self.render_my_choices()
                        return html

                if self.input_type == 'text':
                    html = mark_safe(md2html(self.content.text))
                    if self.content.attachment:
                        # display svg inline
                        html += mark_safe(''.join(self.content.attachment.file.readlines()))
                else:
                    CONF_CHOICES = dict(Response.CONF_CHOICES)
                    is_chat_fsm = (
                        self.chat and
                        self.chat.state and
                        self.chat.state.fsmNode.fsm.fsm_name_is_one_of('chat')  # TODO: add livechat here
                    )
                    values = CONF_CHOICES.values() + EVAL_OPTIONS.values()
                    text_in_values = self.text in values
                    if is_chat_fsm and not self.text:
                        if self.content.selfeval:  # confidence is before selfeval
                            html = EVAL_OPTIONS.get(
                                self.content.selfeval, 'Self evaluation not completed yet'
                            )
                        elif self.content.confidence:
                            html = CONF_CHOICES.get(
                                self.content.confidence, 'Confidence not settled yet'
                            )
                    elif is_chat_fsm and self.text and not text_in_values:
                        html = EVAL_OPTIONS.get(
                            self.text,
                            dict(Response.CONF_CHOICES).get(
                                self.text,
                                self.text
                            )
                        )
            elif self.contenttype == 'unitlesson':
                if self.content.kind == UnitLesson.MISUNDERSTANDS:
                    html = mark_safe(
                        md2html(
                            '**%s** \n %s' %
                            (self.content.lesson.title, self.content.lesson.text)
                        )
                    )
                elif self.input_type == 'options' and self.text: # and not self.content.lesson.sub_kind:
                    html = STATUS_OPTIONS[self.text]
                elif self.content.lesson.sub_kind == Lesson.MULTIPLE_CHOICES:
                    # render unitlesson (question)
                    if self.content.kind == 'part':
                        html = mark_safe(
                            md2html(
                                self.content.lesson.get_choices_wrap_text()
                            )
                        )
                        html += self.get_choices()
                elif self.content.lesson.sub_kind == Lesson.CANVAS:
                    # adds canvas to draw svg image
                    messages = self.chat.message_set.filter(id__gt=self.id)
                    lesson_kwargs = {}
                    try:
                        response = messages[0].content
                        lesson_kwargs['attachment'] = response.attachment
                    except AttributeError:
                        pass
                    html = self.content.lesson.get_html(**lesson_kwargs)
                elif (self.content.kind == 'answers' and
                      self.content.parent.lesson.sub_kind and
                      self.content.parent.lesson.sub_kind == Lesson.MULTIPLE_CHOICES
                    ):
                    if not self.response_to_check.selfeval:
                        correct = self.content.parent.lesson.get_correct_choices()
                        html = self.render_choices(correct, [])
                    elif self.response_to_check.selfeval and self.response_to_check.confidence:
                        correct = self.content.parent.lesson.get_correct_choices()
                        html = self.render_choices(correct, [])
                else:
                    if self.content.lesson.url:
                        raw_html = u'`Read more <{0}>`_ \n\n{1}'.format(
                            self.content.lesson.url,
                            self.content.lesson.text
                        )
                    else:
                        raw_html = self.content.lesson.text

                    html = mark_safe(md2html(raw_html))

                    if self.content.lesson.attachment:
                        # append svg attachment to the message
                        html += mark_safe(''.join(self.content.lesson.attachment.file.readlines()))
            elif self.contenttype == 'uniterror':
                html = self.get_errors()
        if html is None:
            html = (
                self.content.selfeval
                if self.content.selfeval else
                str(self.content)
            )

        return html

    def get_name(self):
        name = "Kris Lee"
        if self.content_id:
            if self.contenttype == 'response':
                name = self.content.author.get_full_name() or self.content.author.username
            elif self.contenttype == 'unitlesson':
                name = self.content.addedBy.get_full_name() or self.content.addedBy.username
            elif self.contenttype == 'chatdivider' and self.content.unitlesson:
                name = (
                    self.content.unitlesson.addedBy.get_full_name() or
                    self.content.unitlesson.addedBy.username
                )
        return name


class EnrollUnitCode(models.Model):
    """
    Model contains links between enrollCode and Units.
    """
    enrollCode = models.CharField(max_length=32, default=enroll_generator)
    courseUnit = models.ForeignKey(CourseUnit)
    isLive = models.BooleanField(default=False)

    class Meta:
        unique_together = ('enrollCode', 'courseUnit', 'isLive')

    @classmethod
    def create_new(cls, course_unit, isLive):
        enroll_code = EnrollUnitCode(
            courseUnit=course_unit,
            isLive=True,
            enrollCode=uuid4().hex
        )
        enroll_code.save()
        return enroll_code

    @classmethod
    def get_code(cls, course_unit, isLive=False):
        enroll_code, cr = cls.objects.get_or_create(courseUnit=course_unit, isLive=isLive)
        if cr:
            enroll_code.enrollCode = uuid4().hex
            enroll_code.isLive = isLive
            enroll_code.save()
        return enroll_code.enrollCode

    @classmethod
    def get_code_for_user_chat(cls, course_unit, is_live, user):
        # enroll = cls(course_unit=course_unit, isLive=is_live)
        enroll = cls.objects.filter(courseUnit=course_unit, isLive=is_live, chat__user=user).first()
        if enroll:
            return enroll
        enroll = cls(courseUnit=course_unit, isLive=is_live)
        enroll.save()
        if not enroll.enrollCode:
            enroll.enrollCode = cls.get_code(courseUnit=course_unit, isLive=is_live)
            enroll.save()
            return enroll
        return enroll


class UnitError(models.Model):
    """
    Model contains links between unit and user error models.
    """
    unit = models.ForeignKey(Unit)
    response = models.ForeignKey(Response)

    def get_errors(self):
        unit_lesson = self.response.unitLesson
        error_list = list(unit_lesson.get_errors())
        # Change this to real check
        if unit_lesson.lesson.add_unit_aborts or not error_list:
            error_list += self.unit.get_aborts()
        return error_list

    def save_response(self, user, response_list):
        if user == self.response.author:
            status = self.response.status
        else:
            status = NEED_REVIEW_STATUS
        for emID in response_list:
            em = UnitLesson.objects.get(pk=int(emID))
            self.response.studenterror_set.create(
                errorModel=em,
                author=user,
                status=status
            )

    @classmethod
    def get_by_message(cls, message):
        message_with_content = Message.objects.filter(chat=message.chat,
                                                      kind='response',
                                                      timestamp__lte=message.timestamp)\
                                              .order_by('-timestamp').first()
        if message.chat and isinstance(message_with_content.content, Response):
            return cls.objects.get_or_create(
                unit=message_with_content.chat.enroll_code.courseUnit.unit,
                response=message_with_content.content
            )[0]
        else:
            raise AttributeError


class ChatDivider(models.Model):
    text = models.CharField(max_length=200)
    unitlesson = models.ForeignKey(UnitLesson, null=True)
