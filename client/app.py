import grpc
import json
import os

# Import the generated gRPC classes
import json_streaming_pb2
import json_streaming_pb2_grpc

# --- Configuration ---
SERVER_ADDRESS = "localhost:50052"
CHUNK_SIZE = 4096  # 4KB

def generate_chunks(file_path, filename):
    """
    A generator function that reads a file and yields chunks of bytes.
    This is used for client-side streaming.
    """
    try:
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield json_streaming_pb2.JsonChunk(data=chunk)
        print(f"Finished reading file '{filename}' for streaming.")
    except FileNotFoundError:
        print(f"Error: File '{file_path}' not found.")
        return


def upload_file(stub, file_path, object_name):
    """
    Uploads a file to the server using client-side streaming RPC.
    """
    print(f"\n--- Uploading {object_name} ---")
    if not os.path.exists(file_path):
        print(f"File not found at: {file_path}")
        return

    # The filename is sent as metadata
    metadata = [('filename', object_name)]
    
    # Create a generator for streaming file chunks
    chunk_generator = generate_chunks(file_path, object_name)
    
    try:
        response = stub.UploadJson(chunk_generator, metadata=metadata)
        if response.success:
            print("gRPC response: Upload successful.")
            print(f"Server message: {response.message}")
        else:
            print(f"gRPC response: Upload failed: {response.message}")
    except grpc.RpcError as e:
        print(f"An RPC error occurred during upload: {e.code()} - {e.details()}")


def download_file(stub, object_id, save_path):
    """
    Downloads a file from the server using server-side streaming RPC.
    """
    print(f"\n--- Downloading {object_id} ---")
    
    # The request now only needs the object_id
    # CORRECTED LINE: The field is 'object_id', not 'message'.
    request = json_streaming_pb2.GetRequest(message=object_id)
    
    try:
        # The RPC call returns an iterator
        response_iterator = stub.GetJson(request)
        
        # Write the streamed chunks to a local file
        with open(save_path, "wb") as f:
            for chunk in response_iterator:
                f.write(chunk.data)
        
        print(f"File downloaded successfully and saved to '{save_path}'")

    except grpc.RpcError as e:
        print(f"An RPC error occurred during download: {e.code()} - {e.details()}")
        # If the file couldn't be downloaded, remove the empty save file
        if os.path.exists(save_path):
            os.remove(save_path)
            

def create_sample_file(file_path):
    """Creates a sample JSON file for testing."""
    if not os.path.exists(file_path):
        print(f"Creating a sample JSON file at '{file_path}'")
        data = {
            "id": "12345",
            "user": "test_user",
            "data": list(range(10000)), # Make it reasonably large
            "message": "This is a test file for gRPC streaming."
        }
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)


if __name__ == '__main__':
    # Don't forget to regenerate the gRPC code after changing the .proto file!
    # python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. json_streaming.proto

    # Create a sample file to upload
    sample_filename = "sample_data.json"
    create_sample_file(sample_filename)

    # Establish a connection to the gRPC server
    with grpc.insecure_channel(SERVER_ADDRESS) as channel:
        # Create a stub (client)
        stub = json_streaming_pb2_grpc.JsonStreamingServiceStub(channel)
        
        # --- Test Case 1: Upload the file ---
        object_to_stream = "my_streamed_file.json"
        upload_file(stub, file_path=sample_filename, object_name=object_to_stream)
        
        # --- Test Case 2: Download the file ---
        download_path = "downloaded_streamed_file.json"
        download_file(stub, object_id=object_to_stream, save_path=download_path)

        # --- Test Case 3: Download a non-existent file (to test error handling) ---
        download_file(stub, object_id="non_existent_file.json", save_path="failed_download.json")

    # Clean up created files
    if os.path.exists(sample_filename):
        os.remove(sample_filename)
    if os.path.exists(download_path):
        os.remove(download_path)

