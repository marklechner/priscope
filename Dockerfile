# Use an official Python runtime as a parent image
FROM python:3.9-slim-buster

# Set the working directory in the container
WORKDIR /app

# Create a non-root user
RUN useradd -m appuser

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Make port 80 available to the world outside this container
EXPOSE 80

# Define environment variable
ENV NAME PRIscope

# Run priscope.py when the container launches
ENTRYPOINT ["python", "priscope.py"]
