import logging
import datetime
import csv

from django import http
from django.conf import settings
from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.utils import simplejson
from django.utils.translation import ugettext as _
from django.views.decorators.http import require_http_methods
from django.template.loader import render_to_string
from django.contrib.sites.models import Site

from commonware.decorators import xframe_sameorigin

from links import tasks as links_tasks
from pagination.views import get_pagination_context

from projects import forms as project_forms
from projects.decorators import organizer_required, participation_required
from projects.decorators import can_view_metric_overview, can_view_metric_detail
from projects.decorators import restrict_project_kind
from projects.models import Project, Participation, PerUserTaskCompletion
from projects import drupal

from l10n.urlresolvers import reverse
from relationships.models import Relationship
from links.models import Link
from users.models import UserProfile
from content.models import Page
from schools.models import School
from statuses import forms as statuses_forms
from activity.models import Activity
from activity.views import filter_activities
from activity.schema import verbs
from signups.models import Signup
from tracker.models import update_metrics_cache, get_metrics_axes
from tracker.models import get_user_metrics, get_unauth_metrics

from drumbeat import messages
from users.decorators import login_required

log = logging.getLogger(__name__)


def project_list(request):
    school = None
    project_list_url = reverse('projects_gallery')
    if 'school' in request.GET:
        try:
            school = School.objects.get(slug=request.GET['school'])
        except School.DoesNotExist:
            return http.HttpResponseRedirect(project_list_url)
    return render_to_response('projects/gallery.html', {'school': school},
                              context_instance=RequestContext(request))


def list_all(request):
    school = None
    directory_url = reverse('projects_directory')
    if 'school' in request.GET:
        try:
            school = School.objects.get(slug=request.GET['school'])
        except School.DoesNotExist:
            return http.HttpResponseRedirect(directory_url)
    projects = Project.objects.filter(not_listed=False).order_by('name')
    if school:
        projects = projects.filter(school=school)
    context = {'school': school, 'directory_url': directory_url}
    context.update(get_pagination_context(request, projects, 24))
    return render_to_response('projects/directory.html', context,
        context_instance=RequestContext(request))


@login_required
def create(request):
    user = request.user.get_profile()
    if request.method == 'POST':
        form = project_forms.ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            act = Activity(actor=user,
                verb=verbs['post'],
                scope_object=project,
                target_object=project)
            act.save()
            participation = Participation(project=project, user=user,
                organizing=True)
            participation.save()
            new_rel, created = Relationship.objects.get_or_create(source=user,
                target_project=project)
            new_rel.deleted = False
            new_rel.save()
            detailed_description_content = render_to_string(
                "projects/detailed_description_initial_content.html",
                {})
            detailed_description = Page(title=_('Full Description'),
                slug='full-description', content=detailed_description_content,
                listed=False, author_id=user.id, project_id=project.id)
            if project.category != Project.STUDY_GROUP:
                detailed_description.collaborative = False
            detailed_description.save()
            project.detailed_description_id = detailed_description.id
            sign_up = Signup(author_id=user.id, project_id=project.id)
            sign_up.save()
            project.create()
            messages.success(request,
                _('The %s has been created.') % project.kind.lower())
            return http.HttpResponseRedirect(reverse('projects_show', kwargs={
                'slug': project.slug,
            }))
        else:
            msg = _("Problem creating the study group, course, ...")
            messages.error(request, msg)
    else:
        form = project_forms.ProjectForm()
    return render_to_response('projects/project_edit_summary.html', {
        'form': form, 'new_tab': True,
    }, context_instance=RequestContext(request))


def matching_kinds(request):
    if len(request.GET['term']) == 0:
        matching_kinds = Project.objects.values_list('kind').distinct()
    else:
        matching_kinds = Project.objects.filter(
            kind__icontains=request.GET['term']).values_list('kind').distinct()
    json = simplejson.dumps([kind[0] for kind in matching_kinds])

    return http.HttpResponse(json, mimetype="application/x-javascript")


def show(request, slug):
    project = get_object_or_404(Project, slug=slug)
    is_organizing = project.is_organizing(request.user)
    is_participating = project.is_participating(request.user)
    is_following = project.is_following(request.user)
    if is_organizing:
        form = statuses_forms.ImportantStatusForm()
    elif is_participating:
        form = statuses_forms.StatusForm()
    else:
        form = None

    show_all_tasks = (project.category == Project.CHALLENGE)

    activities = project.activities()
    activities = filter_activities(request, activities)

    context = {
        'project': project,
        'participating': is_participating,
        'following': is_following,
        'organizing': is_organizing,
        'show_all_tasks': show_all_tasks,
        'form': form,
        'is_challenge': (project.category == Project.CHALLENGE),
        'domain': Site.objects.get_current().domain,
    }
    context.update(get_pagination_context(request, activities))
    return render_to_response('projects/project.html', context,
                              context_instance=RequestContext(request))


@login_required
def clone(request):
    user = request.user.get_profile()
    if request.method == 'POST':
        form = project_forms.CloneProjectForm(request.POST)
        if form.is_valid():
            base_project = form.cleaned_data['project']
            project = Project(name=base_project.name, category=base_project.category,
                other=base_project.other, other_description=base_project.other_description,
                short_description=base_project.short_description,
                long_description=base_project.long_description,
                clone_of=base_project)
            project.save()
            act = Activity(actor=user,
                verb=verbs['post'],
                scope_object=project,
                target_object=project)
            act.save()
            participation = Participation(project=project, user=user,
                organizing=True)
            participation.save()
            new_rel, created = Relationship.objects.get_or_create(source=user,
                target_project=project)
            new_rel.deleted = False
            new_rel.save()
            detailed_description = Page(title=_('Full Description'),
                slug='full-description',
                content=base_project.detailed_description.content,
                listed=False, author_id=user.id, project_id=project.id)
            detailed_description.save()
            project.detailed_description_id = detailed_description.id
            base_sign_up = base_project.sign_up.get()
            sign_up = Signup(public=base_sign_up.public,
                between_participants=base_sign_up.between_participants,
                author_id=user.id, project_id=project.id)
            sign_up.save()
            project.save()
            tasks = Page.objects.filter(project=base_project, listed=True,
                deleted=False).order_by('index')
            for task in tasks:
                new_task = Page(title=task.title, content=task.content,
                    author=user, project=project)
                new_task.save()
            links = Link.objects.filter(project=base_project).order_by('index')
            for link in links:
                new_link = Link(name=link.name, url=link.url, user=user,
                    project=project)
                new_link.save()
            project.create()
            messages.success(request,
                _('The %s has been cloned.') % project.kind.lower())
            return http.HttpResponseRedirect(reverse('projects_show', kwargs={
                'slug': project.slug,
            }))
        else:
            messages.error(request,
                _("There was a problem cloning the study group, course, ..."))
    else:
        form = project_forms.CloneProjectForm()
    return render_to_response('projects/project_clone.html', {
        'form': form, 'clone_tab': True,
    }, context_instance=RequestContext(request))


def matching_projects(request):
    if len(request.GET['term']) == 0:
        raise http.Http404

    matching_projects = Project.objects.filter(
        slug__icontains=request.GET['term'])
    json = simplejson.dumps([project.slug for project in matching_projects])

    return http.HttpResponse(json, mimetype="application/x-javascript")


@login_required
def import_from_old_site(request):
    user = request.user.get_profile()
    if request.method == 'POST':
        form = project_forms.ImportProjectForm(request.POST)
        if form.is_valid():
            course = form.cleaned_data['course']
            project = Project(name=course['name'], kind=course['kind'],
                short_description=course['short_description'],
                long_description=course['long_description'],
                imported_from=course['slug'])
            project.save()
            act = Activity(actor=user,
                verb=verbs['post'],
                scope_object=project,
                target_object=project)
            act.save()
            participation = Participation(project=project, user=user,
                organizing=True)
            participation.save()
            new_rel, created = Relationship.objects.get_or_create(source=user,
                target_project=project)
            new_rel.deleted = False
            new_rel.save()
            if course['detailed_description']:
                detailed_description_content = course['detailed_description']
            else:
                detailed_description_content = render_to_string(
                    "projects/detailed_description_initial_content.html",
                    {})
            detailed_description = Page(title=_('Full Description'),
                slug='full-description', content=detailed_description_content,
                listed=False, author_id=user.id, project_id=project.id)
            detailed_description.save()
            project.detailed_description_id = detailed_description.id
            sign_up = Signup(between_participants=course['sign_up'],
                author_id=user.id, project_id=project.id)
            sign_up.save()
            project.save()
            for title, content in course['tasks']:
                new_task = Page(title=title, content=content, author=user,
                    project=project)
                new_task.save()
            for name, url in course['links']:
                new_link = Link(name=name, url=url, user=user, project=project)
                new_link.save()
            project.create()
            messages.success(request,
                _('The %s has been imported.') % project.kind.lower())
            return http.HttpResponseRedirect(reverse('projects_show', kwargs={
                'slug': project.slug,
            }))
        else:
            msg = _("Problem importing the study group, course, ...")
            messages.error(request, msg)
    else:
        form = project_forms.ImportProjectForm()
    return render_to_response('projects/project_import.html', {
        'form': form, 'import_tab': True},
        context_instance=RequestContext(request))


def matching_courses(request):
    if len(request.GET['term']) == 0:
        raise http.Http404

    matching_nodes = drupal.get_matching_courses(term=request.GET['term'])
    json = simplejson.dumps(matching_nodes)

    return http.HttpResponse(json, mimetype="application/x-javascript")


@login_required
@organizer_required
def edit(request, slug):
    project = get_object_or_404(Project, slug=slug)
    if request.method == 'POST':
        form = project_forms.ProjectForm(request.POST, instance=project)
        if form.is_valid():
            form.save()
            messages.success(request,
                _('%s updated!') % project.kind.capitalize())
            return http.HttpResponseRedirect(
                reverse('projects_edit', kwargs=dict(slug=project.slug)))
    else:
        form = project_forms.ProjectForm(instance=project)

    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser

    return render_to_response('projects/project_edit_summary.html', {
        'form': form,
        'project': project,
        'school': project.school,
        'summary_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
        'is_challenge': (project.category == project.CHALLENGE),
    }, context_instance=RequestContext(request))


@login_required
@xframe_sameorigin
@organizer_required
@require_http_methods(['POST'])
def edit_image_async(request, slug):
    project = get_object_or_404(Project, slug=slug)
    form = project_forms.ProjectImageForm(request.POST, request.FILES,
                                          instance=project)
    if form.is_valid():
        instance = form.save()
        return http.HttpResponse(simplejson.dumps({
            'filename': instance.image.name,
        }))
    return http.HttpResponse(simplejson.dumps({
        'error': 'There was an error uploading your image.',
    }))


@login_required
@organizer_required
def edit_image(request, slug):
    project = get_object_or_404(Project, slug=slug)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser

    if request.method == 'POST':
        form = project_forms.ProjectImageForm(request.POST, request.FILES,
                                              instance=project)
        if form.is_valid():
            messages.success(request, _('Image updated'))
            form.save()
            return http.HttpResponseRedirect(reverse('projects_show', kwargs={
                'slug': project.slug,
            }))
        else:
            messages.error(request,
                           _('There was an error uploading your image'))
    else:
        form = project_forms.ProjectImageForm(instance=project)
    return render_to_response('projects/project_edit_image.html', {
        'project': project,
        'form': form,
        'image_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
        'is_challenge': (project.category == project.CHALLENGE),
    }, context_instance=RequestContext(request))


@login_required
@organizer_required
@restrict_project_kind(Project.STUDY_GROUP, Project.COURSE)
def edit_links(request, slug):
    project = get_object_or_404(Project, slug=slug)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    profile = request.user.get_profile()
    if request.method == 'POST':
        form = project_forms.ProjectLinksForm(request.POST)
        if form.is_valid():
            link = form.save(commit=False)
            link.project = project
            link.user = profile
            link.save()
            messages.success(request, _('Link added.'))
            return http.HttpResponseRedirect(
                reverse('projects_edit_links', kwargs=dict(slug=project.slug)))
        else:
            messages.error(request, _('There was an error adding your link.'))
    else:
        form = project_forms.ProjectLinksForm()
    links = Link.objects.select_related('subscription').filter(project=project)
    return render_to_response('projects/project_edit_links.html', {
        'project': project,
        'form': form,
        'links': links,
        'links_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
    }, context_instance=RequestContext(request))


@login_required
@organizer_required
@restrict_project_kind(Project.STUDY_GROUP, Project.COURSE)
def edit_links_edit(request, slug, link):
    link = get_object_or_404(Link, id=link)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    form = project_forms.ProjectLinksForm(request.POST or None, instance=link)
    profile = get_object_or_404(UserProfile, user=request.user)
    project = get_object_or_404(Project, slug=slug)
    if link.project != project:
        return http.HttpResponseForbidden(_("You can't edit this link"))
    if form.is_valid():
        if link.subscription:
            links_tasks.UnsubscribeFromFeed.apply_async(args=(link,))
            link.subscription = None
            link.save()
        link = form.save(commit=False)
        link.user = profile
        link.project = project
        link.save()
        messages.success(request, _('Link updated.'))
        return http.HttpResponseRedirect(
            reverse('projects_edit_links', kwargs=dict(slug=project.slug)))
    else:
        form = project_forms.ProjectLinksForm(instance=link)
    return render_to_response('projects/project_edit_links_edit.html', {
        'project': project,
        'form': form,
        'link': link,
        'links_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
    }, context_instance=RequestContext(request))


@login_required
@organizer_required
@restrict_project_kind(Project.STUDY_GROUP, Project.COURSE)
def edit_links_delete(request, slug, link):
    if request.method == 'POST':
        project = get_object_or_404(Project, slug=slug)
        link = get_object_or_404(Link, pk=link)
        if link.project != project:
            return http.HttpResponseForbidden(_("You can't edit this link"))
        link.delete()
        messages.success(request, _('The link was deleted'))
    return http.HttpResponseRedirect(
        reverse('projects_edit_links', kwargs=dict(slug=slug)))


@login_required
@organizer_required
def edit_participants(request, slug):
    project = get_object_or_404(Project, slug=slug)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    if request.method == 'POST':
        form = project_forms.ProjectAddParticipantForm(project, request.POST)
        if form.is_valid():
            user = form.cleaned_data['user']
            organizing = form.cleaned_data['organizer']
            participation = Participation(project=project, user=user,
                organizing=organizing)
            participation.save()
            new_rel, created = Relationship.objects.get_or_create(
                source=user, target_project=project)
            new_rel.deleted = False
            new_rel.save()
            messages.success(request, _('Participant added.'))
            return http.HttpResponseRedirect(reverse(
                'projects_edit_participants',
                kwargs=dict(slug=project.slug)))
        else:
            messages.error(request,
                _('There was an error adding the participant.'))
    else:
        form = project_forms.ProjectAddParticipantForm(project)
    return render_to_response('projects/project_edit_participants.html', {
        'project': project,
        'form': form,
        'participations': project.participants().order_by('joined_on'),
        'participants_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
        'is_challenge': (project.category == project.CHALLENGE),
    }, context_instance=RequestContext(request))


def matching_non_participants(request, slug):
    project = get_object_or_404(Project, slug=slug)
    if len(request.GET['term']) == 0:
        raise http.Http404

    non_participants = UserProfile.objects.filter(deleted=False).exclude(
        id__in=project.participants().values('user_id'))
    matching_users = non_participants.filter(
        username__icontains=request.GET['term'])
    json = simplejson.dumps([user.username for user in matching_users])

    return http.HttpResponse(json, mimetype="application/x-javascript")


@login_required
@organizer_required
def edit_participants_make_organizer(request, slug, username):
    participation = get_object_or_404(Participation,
            project__slug=slug, user__username=username, left_on__isnull=True)
    if participation.organizing or request.method != 'POST':
        return http.HttpResponseForbidden(
            _("You can't make that person an organizer"))
    participation.organizing = True
    participation.save()
    messages.success(request, _('The participant is now an organizer.'))
    return http.HttpResponseRedirect(reverse('projects_edit_participants',
        kwargs=dict(slug=participation.project.slug)))


@login_required
@organizer_required
@restrict_project_kind(Project.STUDY_GROUP, Project.COURSE)
def edit_participants_delete(request, slug, username):
    participation = get_object_or_404(Participation,
            project__slug=slug, user__username=username, left_on__isnull=True)
    if request.method == 'POST':
        participation.left_on = datetime.datetime.now()
        participation.save()
        msg = _("The participant %s has been removed.")
        messages.success(request, msg % participation.user)
    return http.HttpResponseRedirect(reverse(
        'projects_edit_participants',
        kwargs={'slug': participation.project.slug}))


@login_required
@organizer_required
def edit_status(request, slug):
    project = get_object_or_404(Project, slug=slug)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    if request.method == 'POST':
        form = project_forms.ProjectStatusForm(
            request.POST, instance=project)
        if form.is_valid():
            form.save()
            return http.HttpResponseRedirect(reverse('projects_show', kwargs={
                'slug': project.slug,
            }))
        else:
            msg = _('There was a problem saving the %s\'s status.')
            messages.error(request, msg % project.kind.lower())
    else:
        form = project_forms.ProjectStatusForm(instance=project)
    return render_to_response('projects/project_edit_status.html', {
        'form': form,
        'project': project,
        'status_tab': True,
        'can_view_metric_overview': can_view_metric_overview,
        'is_challenge': (project.category == project.CHALLENGE),
    }, context_instance=RequestContext(request))


@login_required
@can_view_metric_overview
def admin_metrics(request, slug):
    """Overview metrics for course organizers.

    We only are interested in the pages of the course and the participants.
    """
    project = get_object_or_404(Project, slug=slug)
    can_view_metric_overview = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    can_view_metric_detail = request.user.username in settings.STATISTICS_COURSE_CAN_VIEW_CSV or request.user.is_superuser
    update_metrics_cache(project)
    dates, page_paths = get_metrics_axes(project)
    data = []
    participants = (participant.user for participant in project.non_organizer_participants())
    for row in get_user_metrics(project, participants, dates, page_paths, overview=True):
        data.append({
            'username': row[0],
            'last_active': row[1],
            'course_activity_minutes': row[2],
            'comment_count': row[3],
            'task_edits_count': row[4],
        })

    return render_to_response('projects/project_admin_metrics.html', {
            'project': project,
            'can_view_metric_detail': can_view_metric_detail,
            'data': data,
            'metrics_tab': True,
            'can_view_metric_overview': can_view_metric_overview,
            'is_challenge': (project.category == project.CHALLENGE),
    }, context_instance=RequestContext(request))


@login_required
@can_view_metric_detail
def export_detailed_csv(request, slug):
    """Display detailed CSV for certain users."""

    project = get_object_or_404(Project, slug=slug)
    update_metrics_cache(project)
    page_paths, dates = get_metrics_axes(project)
    # Create csv response
    response = http.HttpResponse(mimetype='text/csv')
    response['Content-Disposition'] = 'attachment; filename=detailed_report.csv'
    writer = csv.writer(response)
    writer.writerow(["Course: " + project.name])
    writer.writerow(["Data generated: " + datetime.datetime.now().strftime("%b %d, %Y")])
    writer.writerow([])
    # Write first row of headers
    row = []
    row.append("Users")
    page_path_headers = ((page_path, "", "") for page_path in page_paths)
    for date in dates:
        row.append(date.strftime("%Y-%m-%d"))
        row.extend([""] * 4)
        row.extend(page_path_headers)
    row.append("TOTAL")
    row.extend([""] * 4)
    row.extend(page_path_headers)
    writer.writerow(row)
    # Write sencond row of headers
    row = []
    row.append("")
    peruser_headers = ("Time on Course Pages", "Non-Zero Length Views",
        "Zero Length Views", "Comments", "Task Edits")
    pageviews_header = ("Time on Page", "Non-Zero Length Views", "Zero Length Views")
    pageviews_headers = pageviews_header * len(page_paths)
    for i in xrange(len(dates)):
        row.extend(peruser_headers)
        row.extend(pageviews_headers)
    row.extend(peruser_headers)
    row.extend(pageviews_headers)
    writer.writerow(row)
    # Write metrics data
    writer.writerow(["Participants"])
    participants = (participant.user for participant in project.non_organizer_participants())
    for row in get_user_metrics(project, participants, dates, page_paths):
        writer.writerow(row)
    writer.writerow(["Followers"])
    followers = (follower.source for follower in project.non_participant_followers())
    for row in get_user_metrics(project, followers, dates, page_paths):
        writer.writerow(row)
    writer.writerow(["Non-Participants"])
    # Previous followers (includes previous participants that are no longer followers)
    previous_followers = (previous.source for previous in project.previous_followers())
    for row in get_user_metrics(project, previous_followers, dates, page_paths):
        writer.writerow(row)
    for row in get_unauth_metrics(project, dates, page_paths):
        writer.writerow(row)

    return response


@login_required
@restrict_project_kind(Project.STUDY_GROUP, Project.COURSE)
def contact_organizers(request, slug):
    project = get_object_or_404(Project, slug=slug)
    if request.method == 'POST':
        form = project_forms.ProjectContactOrganizersForm(request.POST)
        if form.is_valid():
            form.save(sender=request.user)
            messages.info(request,
                          _("Message successfully sent."))
            return http.HttpResponseRedirect(project.get_absolute_url())
    else:
        form = project_forms.ProjectContactOrganizersForm()
        form.fields['project'].initial = project.pk
    return render_to_response('projects/contact_organizers.html', {
        'form': form,
        'project': project,
    }, context_instance=RequestContext(request))


def task_list(request, slug):
    project = get_object_or_404(Project, slug=slug)
    if project.category == Project.CHALLENGE:
        return http.HttpResponseRedirect(project.get_absolute_url())
    tasks = Page.objects.filter(project__pk=project.pk, listed=True,
        deleted=False).order_by('index')
    context = {
        'project': project,
        'tasks': tasks,
    }
    return render_to_response('projects/project_task_list.html', context,
        context_instance=RequestContext(request))


def user_list(request, slug):
    """Display full list of users for the project."""
    project = get_object_or_404(Project, slug=slug)
    participants = project.non_organizer_participants()
    followers = project.non_participant_followers()
    projects_users_url = reverse('projects_user_list',
        kwargs=dict(slug=project.slug))
    context = {
        'project': project,
        'organizers': project.organizers(),
        'projects_users_url': projects_users_url,
    }
    context.update(get_pagination_context(request, participants, 24,
        prefix='participants_'))
    context.update(get_pagination_context(request, followers, 24,
        prefix='followers_'))
    return render_to_response('projects/project_user_list.html', context,
        context_instance=RequestContext(request))


@login_required
@participation_required
@restrict_project_kind(Project.CHALLENGE)
def toggle_task_completion(request, slug, page_slug):
    page = get_object_or_404(Page, project__slug=slug, slug=page_slug,
        listed=True, deleted=False)
    profile = request.user.get_profile()
    if request.method == 'POST':
        try:
            task_completion = PerUserTaskCompletion.objects.get(
                user=profile, page=page, unchecked_on__isnull=True)
            task_completion.unchecked_on = datetime.datetime.today()
            task_completion.save()
        except PerUserTaskCompletion.DoesNotExist:
            task_completion = PerUserTaskCompletion(user=profile, page=page)
            task_completion.save()
        total_count = Page.objects.filter(project__slug=slug, listed=True,
            deleted=False).count()
        completed_count = PerUserTaskCompletion.objects.filter(page__project__slug=slug,
            page__deleted=False, unchecked_on__isnull=True, user=profile).count()
        progressbar_value = (completed_count * 100 / total_count) if total_count else 0
        json = simplejson.dumps({
            'total_count': total_count,
            'completed_count': completed_count,
            'progressbar_value': progressbar_value})
        return http.HttpResponse(json, mimetype="application/json")
