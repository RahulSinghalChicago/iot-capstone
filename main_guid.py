# coding=utf-8
import os
import argparse
import blobconverter
import cv2
import depthai as dai
import numpy as np
from MultiMsgSync import TwoStageHostSeqSync
import uuid
from datetime import datetime
import boto3
import socket
import time
from collections import defaultdict

# Initialize Boto3 S3
hostname = socket.gethostname()
s3_client = boto3.client("s3")
s3_resource = boto3.resource("s3")
bucket_name = "cs437sp23capstone"

parser = argparse.ArgumentParser()
parser.add_argument(
    "-name", "--name", type=str, help="Name of the person for database saving"
)
parser.add_argument(
    "--skip_every_det", type=int, default=1, help="Skip detection every N frames"
)
parser.add_argument(
    "--skip_every_show", type=int, default=1, help="Skip showing frames every N frames"
)
parser.add_argument(
    "--display_size", type=int, default=400, help="Display size in pixels (square)"
)
parser.add_argument(
    "--time_new_det", type=int, default=5, help="Time in seconds for a new detection"
)
parser.add_argument(
    "--skip_init_det",
    type=int,
    default=10,
    help="Number of detections to skip before reporting this notification",
)
parser.add_argument(
    "--no-display", dest="show_display", action="store_false", help="Disable display"
)


args = parser.parse_args()
print(args)


def frame_norm(frame, bbox):
    normVals = np.full(len(bbox), frame.shape[0])
    normVals[::2] = frame.shape[1]
    return (np.clip(np.array(bbox), 0, 1) * normVals).astype(int)


VIDEO_SIZE = (1072, 1072)
databases = "databases"
if not os.path.exists(databases):
    os.mkdir(databases)


class TextHelper:
    def __init__(self) -> None:
        self.bg_color = (0, 0, 0)
        self.color = (255, 255, 255)
        self.text_type = cv2.FONT_HERSHEY_SIMPLEX
        self.line_type = cv2.LINE_AA

    def putText(self, frame, text, coords):
        cv2.putText(
            frame, text, coords, self.text_type, 1.0, self.bg_color, 4, self.line_type
        )
        cv2.putText(
            frame, text, coords, self.text_type, 1.0, self.color, 2, self.line_type
        )


class FaceRecognition:
    def __init__(self, db_path, name) -> None:
        self.read_db(db_path)
        self.name = name
        self.bg_color = (0, 0, 0)
        self.color = (255, 255, 255)
        self.text_type = cv2.FONT_HERSHEY_SIMPLEX
        self.line_type = cv2.LINE_AA
        self.printed = True

    def cosine_distance(self, a, b):
        if a.shape != b.shape:
            raise RuntimeError("array {} shape not match {}".format(a.shape, b.shape))
        a_norm = np.linalg.norm(a)
        b_norm = np.linalg.norm(b)
        return np.dot(a, b.T) / (a_norm * b_norm)

    def new_recognition(self, results):
        conf = []
        max_ = 0
        label_ = None
        for label in list(self.labels):
            for j in self.db_dic.get(label):
                conf_ = self.cosine_distance(j, results)
                if conf_ > max_:
                    max_ = conf_
                    label_ = label

        conf.append((max_, label_, False))
        name = conf[0]
        # Add entry to existing guid db
        if name[0] <= 0.5 or name[1] is None:
            name = (1 - name[0], "UNKNOWN", False)
        #            name = (1 - name[0], "UNKNOWN", True)
        elif name[0] <= 0.7:
            self.create_db(results, name[1])

        if name[1] == "UNKNOWN":
            guid = None
            if self.name is None:
                guid = str(uuid.uuid4())[:6]  # Generate a 6-character GUID
                name = (name[0], guid, name[2])
            self.create_db(results, guid)
        return name

    def read_db(self, databases_path):
        self.labels = []
        for file in os.listdir(databases_path):
            filename = os.path.splitext(file)
            if filename[1] == ".npz":
                self.labels.append(filename[0])

        self.db_dic = {}
        for label in self.labels:
            with np.load(f"{databases_path}/{label}.npz") as db:
                self.db_dic[label] = [db[j] for j in db.files]

        result = ", ".join(
            [f"{key}:{len(value)}" for key, value in self.db_dic.items()]
        )
        print(f"[{result}]")

    def putText(self, frame, text, coords):
        cv2.putText(
            frame, text, coords, self.text_type, 1, self.bg_color, 4, self.line_type
        )
        cv2.putText(
            frame, text, coords, self.text_type, 1, self.color, 1, self.line_type
        )

    def create_db(self, results, guid=None):
        if guid is not None:
            name = guid
        elif self.name is not None:
            name = self.name
        else:
            print("Reached unexpected edge case")
            return
        print("Saving new embedding... ", name)
        try:
            with np.load(f"{databases}/{name}.npz") as db:
                db_ = [db[j] for j in db.files][:]
        except Exception as e:
            db_ = []
        db_.append(np.array(results))
        np.savez_compressed(f"{databases}/{name}", *db_)
        self.db_dic[name] = db_
        if name not in self.labels:
            self.labels.append(name)
        print(self.labels)
        self.adding_new = False


# region Pipeline

print("Creating pipeline...")
pipeline = dai.Pipeline()

print("Creating Color Camera...")
cam = pipeline.create(dai.node.ColorCamera)
# For ImageManip rotate you need input frame of multiple of 16
cam.setPreviewSize(1072, 1072)
cam.setVideoSize(VIDEO_SIZE)
cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
cam.setInterleaved(False)
cam.setBoardSocket(dai.CameraBoardSocket.RGB)

host_face_out = pipeline.create(dai.node.XLinkOut)
host_face_out.setStreamName("color")
cam.video.link(host_face_out.input)

# ImageManip as a workaround to have more frames in the pool.
# cam.preview can only have 4 frames in the pool before it will
# wait (freeze). Copying frames and setting ImageManip pool size to
# higher number will fix this issue.
copy_manip = pipeline.create(dai.node.ImageManip)
cam.preview.link(copy_manip.inputImage)
copy_manip.setNumFramesPool(20)
copy_manip.setMaxOutputFrameSize(1072 * 1072 * 3)

# ImageManip that will crop the frame before sending it to the Face detection NN node
face_det_manip = pipeline.create(dai.node.ImageManip)
face_det_manip.initialConfig.setResize(300, 300)
copy_manip.out.link(face_det_manip.inputImage)

# NeuralNetwork
print("Creating Face Detection Neural Network...")
face_det_nn = pipeline.create(dai.node.MobileNetDetectionNetwork)
face_det_nn.setConfidenceThreshold(0.5)
face_det_nn.setBlobPath(
    blobconverter.from_zoo(name="face-detection-retail-0004", shaves=6)
)
# Link Face ImageManip -> Face detection NN node
face_det_manip.out.link(face_det_nn.input)

face_det_xout = pipeline.create(dai.node.XLinkOut)
face_det_xout.setStreamName("detection")
face_det_nn.out.link(face_det_xout.input)

# Script node will take the output from the face detection NN as an input and set ImageManipConfig
# to the 'age_gender_manip' to crop the initial frame
script = pipeline.create(dai.node.Script)
script.setProcessor(dai.ProcessorType.LEON_CSS)

face_det_nn.out.link(script.inputs["face_det_in"])
# We also interested in sequence number for syncing
face_det_nn.passthrough.link(script.inputs["face_pass"])

copy_manip.out.link(script.inputs["preview"])

with open("script.py", "r") as f:
    script.setScript(f.read())

print("Creating Head pose estimation NN")

headpose_manip = pipeline.create(dai.node.ImageManip)
headpose_manip.initialConfig.setResize(60, 60)
headpose_manip.setWaitForConfigInput(True)
script.outputs["manip_cfg"].link(headpose_manip.inputConfig)
script.outputs["manip_img"].link(headpose_manip.inputImage)

headpose_nn = pipeline.create(dai.node.NeuralNetwork)
headpose_nn.setBlobPath(
    blobconverter.from_zoo(name="head-pose-estimation-adas-0001", shaves=6)
)
headpose_manip.out.link(headpose_nn.input)

headpose_nn.out.link(script.inputs["headpose_in"])
headpose_nn.passthrough.link(script.inputs["headpose_pass"])

print("Creating face recognition ImageManip/NN")

face_rec_manip = pipeline.create(dai.node.ImageManip)
face_rec_manip.initialConfig.setResize(112, 112)
face_rec_manip.inputConfig.setWaitForMessage(True)

script.outputs["manip2_cfg"].link(face_rec_manip.inputConfig)
script.outputs["manip2_img"].link(face_rec_manip.inputImage)

face_rec_nn = pipeline.create(dai.node.NeuralNetwork)
face_rec_nn.setBlobPath(
    blobconverter.from_zoo(
        name="face-recognition-arcface-112x112", zoo_type="depthai", shaves=6
    )
)
face_rec_manip.out.link(face_rec_nn.input)

arc_xout = pipeline.create(dai.node.XLinkOut)
arc_xout.setStreamName("recognition")
face_rec_nn.out.link(arc_xout.input)

# endregion

with dai.Device(pipeline) as device:
    facerec = FaceRecognition(databases, args.name)
    sync = TwoStageHostSeqSync()
    text = TextHelper()

    queues = {}
    # Create output queues
    for name in ["color", "detection", "recognition"]:
        queues[name] = device.getOutputQueue(name)

    counter = 0
    scale = 1.5
    people_in_frame = {}
    detection_count = defaultdict(int)
    skip_every_det = args.skip_every_det
    skip_every_show = args.skip_every_show
    skip_init_det = args.skip_init_det
    display_size = args.display_size
    time_new_det = args.time_new_det

    while True:
        for name, q in queues.items():
            # Add all msgs (color frames, object detections and face recognitions) to the Sync class.
            if q.has():
                sync.add_msg(q.get(), name)

        msgs = sync.get_msgs()
        if msgs is not None:
            frame = msgs["color"].getCvFrame()
            dets = msgs["detection"].detections

            for i, detection in enumerate(dets):
                bbox = frame_norm(
                    frame,
                    (detection.xmin, detection.ymin, detection.xmax, detection.ymax),
                )
                cv2.rectangle(
                    frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (10, 245, 10), 2
                )

                if counter % skip_every_det == 0:
                    features = np.array(msgs["recognition"][i].getFirstLayerFp16())
                    conf, name, save_face = facerec.new_recognition(features)
                    detection_count[name] += 1

                    if save_face is False and name in people_in_frame:
                        prev_entry_time = people_in_frame[name]
                        time_diff = time.time() - prev_entry_time
                        if time_diff > time_new_det:
                            save_face = True
                            detection_count[name] = 0

                    detection_count[name] += 1
                    if detection_count[name] == skip_init_det:
                        save_face = True

                    if save_face:
                        print(f"Saving new detection {name}")
                        now = datetime.now()
                        timestamp = now.strftime("%Y%m%d_%H%M%S")
                        filename = f"{name}_{timestamp}.jpg"

                        center_x = int((bbox[0] + bbox[2]) / 2)
                        center_y = int((bbox[1] + bbox[3]) / 2)

                        # Calculate the width and height of the output image
                        width = int(scale * (bbox[2] - bbox[0]))
                        height = int(scale * (bbox[3] - bbox[1]))

                        # Calculate the coordinates of the top-left corner of the output image
                        x = max(center_x - int(width / 2), 0)
                        y = max(center_y - int(height / 2), 0)

                        # Crop the output image
                        output_image = frame[y : y + height, x : x + width]
                        cv2.imwrite(f"{databases}/{filename}", output_image)
                        s3_key = f"{hostname}/{filename}"
                        encoded_image = cv2.imencode(".jpg", output_image)[1].tostring()
                        s3_client.put_object(
                            Bucket=bucket_name, Key=s3_key, Body=encoded_image
                        )
                    text.putText(
                        frame, f"{name} {(100*conf):.0f}%", (bbox[0] + 10, bbox[1] + 35)
                    )
                counter += 1

            people_in_frame[name] = time.time()
            if args.show_display and counter % skip_every_show == 0:
                cv2.imshow("color", cv2.resize(frame, (display_size, display_size)))

        if cv2.waitKey(1) == ord("q"):
            break
