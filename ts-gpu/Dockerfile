# going to cuda image 12/23
# transcription stream ts-gpu image
#
# Start from a smaller base image, such as Ubuntu slim or Alpine if possible
FROM nvidia/cuda:12.0.1-cudnn8-runtime-ubuntu22.04


# Set environment variables to reduce interaction during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Update and install necessary packages in a single RUN command and clean up in the same layer
RUN apt-get update && apt-get install -y \
    python3.9 \
    ssh \
    openssh-server \
    ffmpeg \
    python3-pip \
    build-essential \
    cython3 \
    git \
    python3-wheel \
#    nvidia-cuda-toolkit \
#    nvidia-cuda-toolkit-gcc \
    tzdata \
 && rm -rf /var/lib/apt/lists/*

# added to fix sox/numpy error while installing whisper-diarization 03/24/2024
RUN pip install typing_extensions
RUN pip install numpy

# Install whisper-diarization requirements
WORKDIR /ts-gpu

# Copy the content of the local src directory to the working directory
COPY . .

RUN pip install -r requirements.txt

# Copy the required files
COPY ts-control.sh /root/scripts/
COPY transcribe.sh /root/scripts/transcribe.sh
COPY test.wav /home/transcriptionstream/
COPY ts-summarize.py /root/scripts/
COPY auto-summary.py /root/scripts/
COPY update-data.py /root/scripts/
COPY notify.py /root/scripts/
COPY helpers.py /root/scripts/

# Create a new user and setup the environment
RUN useradd -m -p $(openssl passwd -1 nomoresaastax) transcriptionstream \
 && mkdir -p /home/transcriptionstream/incoming \
 && mkdir -p /home/transcriptionstream/transcribed \
 && mkdir -p /home/transcriptionstream/incoming/diarize \
 && mkdir -p /home/transcriptionstream/incoming/transcribe \
 && mkdir -p /home/transcriptionstream/incoming/data \
 && chown -R transcriptionstream:transcriptionstream /home/transcriptionstream/ \
 && sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/g' /etc/ssh/sshd_config

# Run the necessary scripts so we have our transcription models IN the image. Adds to build time for download.
RUN python3 diarize.py -a /home/transcriptionstream/test.wav
RUN whisperx --model large-v3 --language en /home/transcriptionstream/test.wav --compute_type int8

# Expose the SSH port
EXPOSE 22

# Start ssh and run the control script
CMD service ssh start && while true; do bash /root/scripts/ts-control.sh; sleep 5; done
