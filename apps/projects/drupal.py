import logging

from django.db import models
from django.conf import settings
from django.utils.translation import ugettext as _

from users.drupal import get_user
from schools.models import School


log = logging.getLogger(__name__)


DRUPAL_DB = 'drupal_db'
COURSE_TYPE = 'course'
COMPLETE_STATUS = '30_complete'
FILE_PATH_PREFIX = 'sites/$NSITE.dev.p2pu.org/files/'
PROJECT_MISSING_IMG = '/images/project-missing.png'


def get_past_courses(username):
    drupal_user = get_user(username)
    past_courses = []
    if drupal_user:
        og_uids = OgUid.objects.using(DRUPAL_DB).filter(uid=drupal_user.uid)
        for og_uid in og_uids:
            course = Node.objects.using(DRUPAL_DB).get(type=COURSE_TYPE, nid=og_uid.nid)
            ct_course = ContentTypeCourse.objects.using(DRUPAL_DB).get(nid=course.nid)
            if ct_course.field_course_status_value == COMPLETE_STATUS:
                url = settings.DRUPAL_URL + get_slug(course.nid)
                image_url = get_image_url(ct_course.field_course_photo_fid)
                data = {
                    'name': course.title,
                    'url': url,
                    'organizer': og_uid.is_admin,
                    'image_url': image_url,
                }
                past_courses.append(data)
    return past_courses


def get_slug(nid):
    alias = UrlAlias.objects.using(DRUPAL_DB).get(src='node/%s' % nid)
    return alias.dst

def get_course(slug, full=False):
    alias = UrlAlias.objects.using(DRUPAL_DB).get(dst=slug)
    course = {}
    try:
        nid = int(alias.src[len('node/'):])
        node = Node.objects.using(DRUPAL_DB).get(type=COURSE_TYPE, nid=nid)
        course['name'] = node.title
        course['slug'] = slug
        course['url'] = settings.DRUPAL_URL + slug
        if not full:
            return course
        course['school'] = None
        term_node = TermNode.objects.using(DRUPAL_DB).get(nid=nid)
        term_data = TermData.objects.using(DRUPAL_DB).get(tid=term_node.tid)
        try:
            school = School.objects.get(old_term_name=term_data.name)
            course['school'] = school
        except School.DoesNotExist:
            pass
        ct_course = ContentTypeCourse.objects.using(DRUPAL_DB).get(nid=nid)
        course['short_description'] = ct_course.field_course_short_desc_value
        course['long_description'] = ''
        course['detailed_description'] = ''
        if ct_course.field_course_summary_value:
            course['detailed_description'] += '<h2>' + _('Summary') + '</h2><br>'
            course['detailed_description'] += ct_course.field_course_summary_value + '<br>'
        if ct_course.field_course_learning_objectives_value:
            course['detailed_description'] += '<h2>' + _('Learning Objectives') + '</h2><br>'
            course['detailed_description'] += ct_course.field_course_learning_objectives_value + '<br>'
        if ct_course.field_course_prerequisites_value:
            course['detailed_description'] += '<h2>' + _('Prerequisites') + '</h2><br>'
            course['detailed_description'] += ct_course.field_course_prerequisites_value + '<br>'
        if ct_course.field_course_sign_up_req_value:
            course['detailed_description'] += '<h2>' + _('Sign-Up Task') + '</h2><br>'
            course['detailed_description'] += ct_course.field_course_sign_up_req_value + '<br>'
        course['tasks'] = []
        course['links'] = []
    except Exception, ex:
        log.error('Course %s not found on the old site: %s' % (slug, ex))
        raise
    return course


def get_matching_courses(school=None, term=None):
    slugs = []
    if school and school.old_term_name:
        term_data = TermData.objects.using(DRUPAL_DB).get(name=school.old_term_name)
        nodes = TermNode.objects.using(DRUPAL_DB).filter(tid=term_data.tid)
    else:
        nodes = Node.objects.using(DRUPAL_DB).filter(type=COURSE_TYPE)
    for node in nodes:
        slug = get_slug(node.nid)
        if term.lower() in slug:
            slugs.append(slug)
    return slugs


def get_image_url(fid):
    if fid:
        f = Files.objects.using(DRUPAL_DB).get(fid=fid)
        if f.filepath.startswith(FILE_PATH_PREFIX):
            return settings.DRUPAL_FILES_URL + f.filepath[len(FILE_PATH_PREFIX):]
    return settings.MEDIA_URL + PROJECT_MISSING_IMG


class Node(models.Model):
    nid = models.IntegerField(primary_key=True)
    vid = models.IntegerField(unique=True)
    type = models.CharField(max_length=96)
    language = models.CharField(max_length=36)
    title = models.CharField(max_length=765)
    uid = models.IntegerField()
    status = models.IntegerField()
    created = models.IntegerField()
    changed = models.IntegerField()
    comment = models.IntegerField()
    promote = models.IntegerField()
    moderate = models.IntegerField()
    sticky = models.IntegerField()
    tnid = models.IntegerField()
    translate = models.IntegerField()
    class Meta:
        db_table = u'node'


class ContentTypeCourse(models.Model):
    vid = models.IntegerField(primary_key=True)
    nid = models.IntegerField()
    field_course_status_value = models.TextField(blank=True)
    field_course_dates_value = models.CharField(max_length=60, blank=True)
    field_course_dates_value2 = models.CharField(max_length=60, blank=True)
    field_course_opening_date_value = models.CharField(max_length=60, blank=True)
    field_course_facilitator_about_value = models.TextField(blank=True)
    field_course_facilitator_about_format = models.IntegerField(null=True, blank=True)
    field_course_photo_fid = models.IntegerField(null=True, blank=True)
    field_course_photo_list = models.IntegerField(null=True, blank=True)
    field_course_photo_data = models.TextField(blank=True)
    field_course_short_desc_value = models.CharField(max_length=420, blank=True)
    field_refers_to_syllabus_nid = models.IntegerField(null=True, blank=True)
    field_course_summary_value = models.TextField(blank=True)
    field_course_summary_format = models.IntegerField(null=True, blank=True)
    field_course_prerequisites_value = models.TextField(blank=True)
    field_course_prerequisites_format = models.IntegerField(null=True, blank=True)
    field_course_sign_up_req_value = models.TextField(blank=True)
    field_course_sign_up_req_format = models.IntegerField(null=True, blank=True)
    field_course_no_of_seats_value = models.IntegerField(null=True, blank=True)
    field_course_learning_objectives_value = models.TextField(blank=True)
    field_course_learning_objectives_format = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = u'content_type_course'


class OgUid(models.Model):
    nid = models.IntegerField(primary_key=True)
    og_role = models.IntegerField()
    is_active = models.IntegerField()
    is_admin = models.IntegerField()
    uid = models.IntegerField(primary_key=True)
    created = models.IntegerField(null=True, blank=True)
    changed = models.IntegerField(null=True, blank=True)
    class Meta:
        db_table = u'og_uid'


class Files(models.Model):
    fid = models.IntegerField(primary_key=True)
    uid = models.IntegerField()
    filename = models.CharField(max_length=765)
    filepath = models.CharField(max_length=765)
    filemime = models.CharField(max_length=765)
    filesize = models.IntegerField()
    status = models.IntegerField()
    timestamp = models.IntegerField()
    origname = models.CharField(max_length=765)
    class Meta:
        db_table = u'files'


class TermData(models.Model):
    tid = models.IntegerField(primary_key=True)
    vid = models.IntegerField()
    name = models.CharField(max_length=765)
    description = models.TextField(blank=True)
    weight = models.IntegerField()
    class Meta:
        db_table = u'term_data'


class TermNode(models.Model):
    nid = models.IntegerField()
    vid = models.IntegerField()
    tid = models.IntegerField(primary_key=True)
    class Meta:
        db_table = u'term_node'


class UrlAlias(models.Model):
    pid = models.IntegerField(primary_key=True)
    src = models.CharField(max_length=384)
    dst = models.CharField(unique=True, max_length=768, blank=True)
    language = models.CharField(max_length=36)
    class Meta:
        db_table = u'url_alias'
