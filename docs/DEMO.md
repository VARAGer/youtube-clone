# Временное рабочее демо

[https://forest-crm-imagine-downloading.trycloudflare.com](https://forest-crm-imagine-downloading.trycloudflare.com)

Демо опубликовано и проверено 17 ноября 2026 года. Можно смотреть открытые
видео без входа. Для загрузки, комментариев, лайков и плейлистов создайте свой
тестовый аккаунт через кнопку «Войти» → «Создать аккаунт».

Это бесплатная временная ссылка Cloudflare Quick Tunnel: она работает, пока
Mac, Docker VM и процесс cloudflared включены и подключены к интернету.
После остановки туннеля ссылка прекращает работать; следующий запуск выдаст
другой адрес. Это не постоянный хостинг и не обещание доступности 24/7.
Описание сервиса: [https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

В демо предел одного файла — **20 MiB**. PostgreSQL и MinIO не опубликованы
наружу; посетитель обращается только к API приложения через HTTPS. Включены
Secure/HttpOnly cookie, проверка Origin и текущие ACL. Нативные AI-сессии,
`.env` и `.git` не доступны через сайт. Пароли администратора и модератора
не опубликованы: они хранятся только в приватном `.env` развертывания.

Проверено через этот внешний HTTPS-адрес: HTTP smoke с ролями и приватностью,
загрузка/кодирование/воспроизведение/перемотка, 3 браузерных сценария,
Secure/HttpOnly/SameSite cookie, выход из аккаунта, отклонение чужого Origin.
Семь снимков desktop/mobile проверил агент GPT-6 Luna.

## Как повторить на своём развертывании

1. Разверните и заполните демо: `bash scripts/deploy.sh`.
2. Установите cloudflared из официального источника и запустите:

```sh
cloudflared tunnel --no-autoupdate --protocol http2 --url http://127.0.0.1:8080
```

3. В приватном `.env` задайте полученный адрес как `PUBLIC_ORIGIN`,
   `SEED_BASE_URL` и `SMOKE_BASE_URL`; включите `COOKIE_SECURE=true`.
4. Пересоздайте app/worker без удаления томов:

```sh
docker compose up -d --no-build --wait app worker
```

При нестандартном порте используйте фактический `PORT` из `.env`.
При нестандартном имени Compose-проекта передавайте его явно.
Сначала заполняйте локальный стек, затем включайте HTTPS-cookie; повторный seed
с HTTPS-cookie должен обращаться через `SEED_BASE_URL` с внешним HTTPS-адресом.
Остановка cloudflared закрывает внешнюю ссылку, сохраняя данные в Docker-томах.
