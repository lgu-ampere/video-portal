import os

from django.shortcuts import render
from .models import Video, Videoprocess
from minio import Minio
from django.conf import settings
from datetime import timedelta
from videoplayer.forms import VideoUploadForm

# from django.shortcuts import render
from django.http import HttpResponseRedirect, StreamingHttpResponse
# from django.conf import settings
# from minio import Minio
from kubernetes import client, config, utils
from time import sleep

from kubernetes.stream import stream
from videoplayer.generate_yaml import generate_yaml_file
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


def getMinIOVideos(request):
    minio_client = Minio(
        settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        secure=False
    )
    bucket_name = 'vpp-dst'
    expiration = timedelta(seconds=432000)  # Expiration time in seconds (5 days)

    # Retrieve the list of objects from the MinIO bucket
    minio_files = minio_client.list_objects('vpp-dst')
    videos = Video.objects.all()
    # list all the video names in videoplayer_video table
    video_list_exist = []
    for video in videos:
        video_list_exist.append(video.title)
    # check if the new video in minio vpp-dst bucket has not been added, add the video
    for file in minio_files:
        title = str(file.object_name[:-1])
        if title not in video_list_exist:
            print('not exist:', file.object_name[:-1])

            video = Video.objects.create(
                title=title,
                mp4_file=title + '/h265/' + title + '_720p.mp4',
                vtt_file=title + '/webvtt/' + title + '.en_US.vtt',
                gif_file=title + '/gif/ani-preview.gif',
                thumbnail_file=title + '/gif/thumbnail.gif')

    for video in videos:
        # print(111,video.mp4_file)
        video.mp4_url = minio_client.presigned_get_object(
            bucket_name,
            video.mp4_file,
            expires=expiration
        )
        # print(222, video.mp4_file)
        # print(333, video.mp4_url)
        video.vtt_url = minio_client.presigned_get_object(
            bucket_name,
            video.vtt_file,
            expires=expiration
        )
        video.gif_url = minio_client.presigned_get_object(
            bucket_name,
            video.gif_file,
            expires=expiration
        )
        video.thumbnail_url = minio_client.presigned_get_object(
            bucket_name,
            video.thumbnail_file,
            expires=expiration
        )

    return videos, minio_files
def video_list(request):
    videos, minio_files = getMinIOVideos(request)
    return render(request, 'videoplayer/video_list.html', {'videos': videos, 'minio_files': minio_files})
def dash(request):
    videos, minio_files = getMinIOVideos(request)
    return render(request, 'videoplayer/dash.html', {'videos': videos, 'minio_files': minio_files})
def hls(request):
    videos, minio_files = getMinIOVideos(request)
    return render(request, 'videoplayer/hls.html', {'videos': videos, 'minio_files': minio_files})
def video_dash(request, video_title):
    context = {
        'video_title': video_title
    }
    return render(request, 'videoplayer/video_dash.html', context)
def video_hls(request, video_title):
    context = {
        'video_title': video_title
    }
    return render(request, 'videoplayer/video_hls.html', context)
def getMinIOVideosURL(file_path, minio_client):
    bucket_name = 'vpp-dst'
    expiration = timedelta(seconds=432000)  # Expiration time in seconds (5 days)
    # minio_files = minio_client.list_objects('vpp-dst')

    file_url = minio_client.presigned_get_object(
            bucket_name,
            file_path,
            expires=expiration
        )

    return file_url
@csrf_exempt
def upload(request):
    if request.method == 'POST':

        form = VideoUploadForm(request.POST, request.FILES)
        if form.is_valid():
            # num = str(form.cleaned_data['num_input'])
            filename = form.cleaned_data['video_file']

            config.load_kube_config('videoplayer/mk8s-kubeconfig-node4')
            k8s_core_v1 = client.CoreV1Api()

            # Initialize MinIO client
            minio_client = Minio(settings.MINIO_ENDPOINT,
                                 access_key=settings.MINIO_ACCESS_KEY,
                                 secret_key=settings.MINIO_SECRET_KEY,
                                 secure=False)








            # Create a generator function to stream messages
            def stream_messages():
                # Start generating messages
                messages1, messages2, messages3 = "", "", ""
                messages1 = step1_upload(filename, minio_client)
                for message in messages1:
                    if isinstance(message, str):
                        # Handle string responses (real-time updates)
                        yield message
                    else:
                        # Handle the videoprocess.uuid value
                        videoprocess_uuid = message
                        print("Videoprocess UUID:", videoprocess_uuid)

                messages2 = step2_process_video(k8s_core_v1, filename, minio_client, videoprocess_uuid)
                for message in messages2:
                    yield message
                messages3 = step3_upload_to_k8s_pod(k8s_core_v1, filename, minio_client)
                for message in messages3:
                    yield message

            # Return the streaming response with "text/event-stream" content type
            response = StreamingHttpResponse(stream_messages(), content_type='text/event-stream')
            return response
        else:
            return JsonResponse({'error': 'Invalid form data'})

    else:
        form = VideoUploadForm()
        return render(request, 'videoplayer/upload.html', {'form': form})


def step1_upload(filename, minio_client):
    msg = ""
    # step1.upload to MinIO
    # upload_video(filename, minio_client)
    video_file = filename

    # Save the uploaded file to a temporary location
    temp_file_path = os.path.join(settings.MEDIA_ROOT, video_file.name)
    with open(temp_file_path, 'wb') as temp_file:
        for chunk in video_file.chunks():
            temp_file.write(chunk)
    msg = 'Uploading Video [' + video_file.name + '] ... wait ...'
    yield msg
    # Upload the file to MinIO
    minio_client.fput_object('vod-poc', video_file.name, temp_file_path)


    # Delete the temporary file
    os.remove(temp_file_path)

    # Retrieve the list of objects from the MinIO bucket
    minio_files = minio_client.list_objects('vod-poc')
    # print(1, filename.name)
    minio_list = [str(file.object_name) for file in minio_files]

    if str(filename.name) in minio_list:
        yield 'Video [' + video_file.name + '] uploaded successfully!'
        # if upload successfully, register for a uuid in table videoprocess
        videoprocess = Videoprocess.objects.create(title=str(filename.name))
        yield videoprocess.uuid
    else:
        yield 'Upload issues'



def step2_process_video(k8s_core_v1, filename, minio_client, videoprocess_uuid):
    video_file = filename
    # step2.process video
    success_message = None
    aApiClient = client.ApiClient()

    # Generate YAML file
    print('num:',videoprocess_uuid)
    num = str(videoprocess_uuid)
    yaml_file = generate_yaml_file(num, str(filename), settings.MINIO_ENDPOINT)
    # msg = "\n\n" + "generate yaml file:\n" + yaml_file + "\n\n"
    # yield msg

    yaml_file = 'videoplayer/job.yaml'
    utils.create_from_yaml(aApiClient, yaml_file, verbose=True, namespace="process-ns")
    msg = "YAML file Applied.\n\n"
    yield msg

    job_completed = False
    while not job_completed:
        api_response = k8s_core_v1.read_namespaced_pod_status(
            name='vpp-app-' + str(num),
            namespace="process-ns")
        if api_response.status.container_statuses is not None:
            for container in api_response.status.container_statuses:
                if container.state is not None and container.state.terminated is not None:
                    job_completed = True
        sleep(8)
        # msg = "Job status='%s'" % str(api_response.status.container_statuses) + "\n\n"
        yield 'Processing Video [' + video_file.name + '] ... wait ...'

    minio_files = minio_client.list_objects('vpp-dst')
    minio_list = [str(file.object_name) for file in minio_files]
    print(minio_list)
    if str(filename.name)[:-4] + '/' in minio_list:
        yield 'Video [' + filename.name + '] processed successfully!'
    else:
        yield 'Process issues'



def step3_upload_to_k8s_pod(k8s_core_v1, filename, minio_client):
    # step3.process video
    # Specify the namespace and Pod name
    namespace = "vod-poc"
    pod_name = "nginx-vod-app-0"

    # get the file name from client*
    video_name = str(filename.name)[:-4]

    # get the vtt MinIO link
    vtt_file_path = video_name + '/webvtt/' + video_name + '.en_US.vtt'
    vtt_url = getMinIOVideosURL(vtt_file_path, minio_client)

    command = ["sh", "-c"]
    cmd = ""
    cmd += " cd /opt/static/videos/"
    # download vtt command
    #
    cmd += " && wget -O {}_.en_US.vtt  \"{}\"".format(video_name, vtt_url)
    # download video command, from 240p to 1080p
    for p in ['_240p', '_360p', '_480p', '_720p', '_1080p']:
        mp4_file_path = video_name + '/h265/' + video_name + p + ".mp4"
        mp4_url = getMinIOVideosURL(mp4_file_path, minio_client)
        cmd += " && wget -O {}{}.mp4  \"{}\"".format(video_name, p, mp4_url)
    # #

    cmd += " && ls -al"
    command.append(cmd)
    print(command)
    # Specify the command you want to run inside the Pod
    try:
        # Execute the command inside the Pod
        response = stream(k8s_core_v1.connect_post_namespaced_pod_exec, pod_name, namespace, stderr=True,
                          stdin=True, stdout=True,
                          command=command)

        # ' 20230623video_.en_US.vtt' will in the 'ls-al' log, but not in the saving log(without the first space)
        expect_log_part = " " + video_name + "_.en_US.vtt"
        if expect_log_part in response:
            yield 'Video [' + filename.name + '] is ready for video streaming!'
        else:
            yield 'vod-poc Pod issues'
    except Exception as e:
        msg = e
        print("Exception when calling CoreV1Api->read_vod-poc_pod_exec:", e)
        yield msg