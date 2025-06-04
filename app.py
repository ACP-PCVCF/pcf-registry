from io import BytesIO
from typing import Tuple

from flask import Flask, request, json, jsonify, Response
from minio import Minio, S3Error
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

MINIO_ENDPOINT = "minio-service:9000"#os.getenv("MINIO_ENDPOINT")
MINIO_ACCESS_KEY = "minio"#os.getenv("ACCESS_KEY")
MINIO_SECRET_KEY = "minioadmin"#os.getenv("SECRET_KEY")
MINIO_BUCKET = "pcf-registry"#os.getenv("MINIO_BUCKET")

minio_client = Minio(
    endpoint=MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

#check if a bucket with the MINIO_BUCKET name already exists, if not create a new one
if not minio_client.bucket_exists(MINIO_BUCKET):
    minio_client.make_bucket(MINIO_BUCKET)

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002)
