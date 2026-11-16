import os
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql+psycopg://youtube:youtube@db:5432/youtube')
STORAGE_BACKEND = os.getenv('STORAGE_BACKEND', 's3')
STORAGE_PATH = os.getenv('STORAGE_PATH', '/tmp/youtube-storage')
S3_ENDPOINT = os.getenv('S3_ENDPOINT', 'http://minio:9000')
S3_BUCKET = os.getenv('S3_BUCKET', 'videos')
S3_ACCESS_KEY = os.getenv('S3_ACCESS_KEY', 'minioadmin')
S3_SECRET_KEY = os.getenv('S3_SECRET_KEY', 'minioadmin')
MAX_UPLOAD_BYTES = int(os.getenv('MAX_UPLOAD_BYTES', str(512 * 1024 * 1024)))
COOKIE_SECURE = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'
SESSION_DAYS = 14
PUBLIC_ORIGIN = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
