import os
import threading
from concurrent import futures
from io import BytesIO
from typing import Tuple

from flask import Flask, request, json, jsonify, Response
from minio import Minio, S3Error
import grpc
import json_streaming_pb2
import json_streaming_pb2_grpc
app = Flask(__name__)

MINIO_ENDPOINT = "minio-service:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "pcf-registry"

minio_client = Minio(
    endpoint=MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

#check if a bucket with the MINIO_BUCKET name already exists, if not create a new one
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)


# ------------------ gRPC Server implementation (upload, get) -------#

def get_file_from_metadata(context):
    for meta_data_chunk in context.invocation_metadata():
        if meta_data_chunk.key == "filename":
            return meta_data_chunk.value
    return None

class JsonStreamingServicer(json_streaming_pb2_grpc.JsonStreamingServiceServicer):

    def UploadJson(self, request_iterator, context):
        filename = get_file_from_metadata(context)
        temp_path = f"/tmp/{filename}"

        with open(temp_path, "wb") as f:
            for chunk in request_iterator:
                f.write(chunk.data)

        try:
            minio_client.fput_object(MINIO_BUCKET, filename, temp_path)
            response = json_streaming_pb2.UploadResponse(success=True, message=f"File {filename} uploaded")
        except S3Error as e:
            response = {"error": str(e)}
        except Exception as e:
            response = {"error": str(e)}

        os.remove(temp_path)
        return response


    def GetJson(self, get_request, context):
        bucket = get_request.bucket
        filename = get_request.filename

        temp_path = f"/tmp/{filename}"
        minio_client.fget_object(bucket, filename, temp_path)

        with open(temp_path, "rb") as f:
            while True:
                chunk = f.read(4096)
                if not chunk:
                    break
                yield json_streaming_pb2.JsonChunk(data=chunk)

        os.remove(temp_path)

# ------------------ End of gRPC Server ------------------------------#


# ------------------ HTTP Server (Crud app) --------------------------#


@app.route('/')
def hello_world():  # put application's code here
    return 'Hello World!'


@app.route('/pcf-registry/<object_name>', methods=['GET'])
def get_file(object_name: str):
    if object_name is None or object_name == "":
        return jsonify({"error": "Missing object name"}), 400

    #get the object from the filestorage and read it into json bytes
    try:
        json_bytes = minio_client.get_object(MINIO_BUCKET, object_name).read()
    except S3Error as e:
        return jsonify({f"error with getting {object_name}": str(e)}), 404
    except Exception as e:
        return jsonify({"Unexpected error": str(e)}), 500

    #transfrom the json bytes into a json dict
    json_data = json.loads(json_bytes.decode('utf-8'))

    return jsonify(json_data), 200


@app.route('/pcf-registry/<object_name>', methods=['POST'])
def post_file(object_name: str) -> Tuple[Response, int]:
    """
    POST request to this MS (url: .../pcf-registry/<object_name>).

    Arguments:
        request: the post request sent to this url
        object_name: the name of the object to be uploaded

    Returns:
        A confirmation of a successfull upload with the objects name in form of a dict
    """

    if object_name is None or object_name == "":
        return jsonify({"error": "Missing object name"}), 400

    #parse the json body from the request
    request_body: dict = request.get_json()

    if request_body is None:
        return jsonify({"error": "Missing request body"}), 401

    #transform the json data to a file object so that it can be stored in minIO
    json_bytes = json.dumps(request_body).encode('utf-8')
    file_object = BytesIO(json_bytes)

    #add the file to the minIO filestorage
    try:
        minio_client.put_object(
            bucket_name=MINIO_BUCKET,
            object_name=object_name,
            data=file_object,
            length=len(json_bytes)
        )
    except S3Error as e:
        return jsonify({f"error with uploading {object_name}": str(e)}), 404
    except Exception as e:
        return jsonify({"Unexpected error": str(e)}), 500

    return jsonify({"message": f"Uploaded {object_name} successfully."}), 200


@app.route('/pcf-registry/<object_name>', methods=['DELETE'])
def delete_file(object_name: str):
    if object_name is None or object_name == "":
        return jsonify({"error": "Missing object name"}), 400

    #delete the object from the filestorage
    try:
        minio_client.remove_object(MINIO_BUCKET, object_name)
        return jsonify({"message": f"Deleted '{object_name}'"}), 200
    except S3Error as e:
        return jsonify({"error": f"MinIO S3 error: {e.code}", "message": str(e)}), 404
    except Exception as e:
        return jsonify({"error": "Unexpected error", "message": str(e)}), 500


@app.route('/pcf-registry/search/<bucket_name>/<object_name>', methods=['GET'])
def check_duplicate(object_name: str, bucket_name: str):
    """
    Check if an object already exists in the minio bucket.
    Args:
        object_name: name of the file
        bucket_name: name of the bucket

    Returns:
        200: objects does not exist yet
        401: object already exists
        400: no objects name given
        501: unexpected error
    """

    if object_name is None or object_name == "":
        return jsonify({"error": "Missing object name"}), 400

    try:
        minio_objects = minio_client.list_objects(bucket_name=bucket_name)
        for minio_object in minio_objects:
            if minio_object.object_name == object_name:
                return jsonify({"message": f"Duplicate '{object_name}'"}), 401
        return jsonify({"message": f"Object '{object_name}' does not exist yet."}), 200

    except Exception as e:
        return jsonify({"error": "Unexpected error", "message": str(e)}), 501

#----------------- End of HTTP Server ------------------#

#starts the grpc server on port 50052
def serve_grpc():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    json_streaming_pb2_grpc.add_JsonStreamingServiceServicer_to_server(JsonStreamingServicer(), server)
    server.add_insecure_port('[::]:50052')
    server.start()
    print("gRPC server listening on port 50052...")
    server.wait_for_termination()


#starts the http flask server
def run_http():
    app.run(port=5002, debug=True)


if __name__ == '__main__':
    t1 = threading.Thread(target=serve_grpc)
    t2 = threading.Thread(target=run_http)

    t1.start()
    t2.start()

    t1.join()
    t2.join()