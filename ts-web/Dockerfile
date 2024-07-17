# transcription stream ts-web image 11/2023
#
# Use an official Python runtime as a parent image
FROM ollama/ollama

RUN : \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        software-properties-common \
    && add-apt-repository -y ppa:deadsnakes \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3.8-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && :

RUN python3.8 -m venv /venv
ENV PATH=/venv/bin:$PATH

# Set the working directory in the docker image
WORKDIR /ts-web

# Copy the content of the local src directory to the working directory
COPY . .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Make port available to the world outside this container
EXPOSE 5000

# Define environment variable
# ENV NAME World

CMD ["bash", "-c", "exec python /ts-web/app.py"]

