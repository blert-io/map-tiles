FROM python:3.10

# Install wget and bash (if not present)
RUN apt-get update && apt-get install -y wget bzip2

# Install miniconda
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh \
    && chmod +x Miniconda3-latest-Linux-x86_64.sh \
    && ./Miniconda3-latest-Linux-x86_64.sh -b -p /usr/local/miniconda3 \
    && rm Miniconda3-latest-Linux-x86_64.sh

ENV PATH="/usr/local/miniconda3/bin:${PATH}"

# Accept Terms of Service for required channels
RUN conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main \
    && conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r

# Create conda environment
RUN conda create --yes --name vips python=3.7

# Use the conda env for all future commands
SHELL ["conda", "run", "--no-capture-output", "-n", "vips", "/bin/bash", "-c"]

# Install vips and other dependencies
RUN conda install --yes -c conda-forge pyvips openjdk=11 maven

# Install python dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir pyvips

# Add your project files
ADD src src
ADD java java

RUN chmod +x java/gradlew
RUN ./java/gradlew -p java build

# Set default command
CMD ["conda", "run", "--no-capture-output", "-n", "vips", "python", "./src/tile_generator.py"]
