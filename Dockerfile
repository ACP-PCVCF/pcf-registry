FROM python:3.11

WORKDIR /usr/src/pcf

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5002 50052

CMD ["flask", "run"]