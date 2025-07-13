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

def get_filename_from_metadata(context):
    """Extracts filename from gRPC invocation metadata."""
    for key, value in context.invocation_metadata():
        if key == "filename":
            return value
    return None

class JsonStreamingServicer(json_streaming_pb2_grpc.JsonStreamingServiceServicer):
    """Implements the gRPC streaming service."""

    def UploadJson(self, request_iterator, context):
        """
        Handles client-streaming upload. The file is written to a temporary
        location and then uploaded to MinIO.
        """
        filename = get_filename_from_metadata(context)
        if not filename:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Filename must be provided in metadata.")
            return json_streaming_pb2.UploadResponse(success=False, message="Missing filename.")

        print("hallllloooo")
        temp_path = f"/tmp/{filename}"
        print(f"Receiving file: {filename}")

        try:
            # Write stream to a temporary file
            with open(temp_path, "wb") as f:
                for chunk in request_iterator:
                    f.write(chunk.data)

            # Upload the completed file from the temporary path to MinIO
            minio_client.fput_object(MINIO_BUCKET, filename, temp_path)
            print(f"File '{filename}' successfully uploaded to MinIO bucket '{MINIO_BUCKET}'.")
            response = json_streaming_pb2.UploadResponse(success=True, message=f"File {filename} uploaded successfully.")
        
        except S3Error as e:
            print(f"MinIO Error during upload: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"MinIO S3 Error: {e}")
            response = json_streaming_pb2.UploadResponse(success=False, message=str(e))
        except Exception as e:
            print(f"An unexpected error occurred during upload: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Internal Server Error: {e}")
            response = json_streaming_pb2.UploadResponse(success=False, message=str(e))
        finally:
            # Clean up the temporary file
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return response

    def GetJson(self, request, context):
        """
        Handles server-streaming download. The file is fetched from MinIO and
        streamed back to the client in chunks. The bucket is fixed.
        """
        filename = request.message
        
        print(f"Request to download '{filename}' from bucket '{MINIO_BUCKET}'.")
        
        temp_path = f"/tmp/{filename}"

        try:
            # Download object from MinIO to a temporary file
            minio_client.fget_object(MINIO_BUCKET, filename, temp_path)
            
            # Stream the file in chunks from the temporary location
            with open(temp_path, "rb") as f:
                while True:
                    chunk = f.read(4096)  # 4KB chunk size
                    if not chunk:
                        break
                    yield json_streaming_pb2.JsonChunk(data=chunk)
            print(f"Finished streaming '{filename}'.")

        except S3Error as e:
            print(f"MinIO Error during download: {e}")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Could not retrieve file. Error: {e}")
            # Yield nothing to indicate an error.
            return
        except Exception as e:
            print(f"An unexpected error occurred during download: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"An unexpected error occurred. Error: {e}")
            # Yield nothing to indicate an error.
            return
        finally:
            # Clean up the temporary file
            if os.path.exists(temp_path):
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


def run_http():
    """
    Starts the Flask HTTP server.
    For production, use a proper WSGI server like Gunicorn or uWSGI.
    When running Flask in a thread, the reloader must be disabled.
    """
    print("Flask server starting...")
    # host='0.0.0.0' makes the server accessible from outside the container.
    # use_reloader=False is CRITICAL for running in a non-main thread.
    app.run(host='0.0.0.0', port=5002, debug=False, use_reloader=False)


if __name__ == '__main__':
    # Running both servers in separate threads for development.
    # For a production environment, it's better to run these as separate services/processes.
    grpc_thread = threading.Thread(target=serve_grpc)
    http_thread = threading.Thread(target=run_http)

    grpc_thread.start()
    http_thread.start()

    print("HTTP and gRPC servers are running in separate threads.")

    grpc_thread.join()
    http_thread.join()

    print("Servers have been terminated.")

