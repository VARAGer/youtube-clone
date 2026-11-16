# API contract v1
Prefix /api. JSON errors {detail: string}. Cookie sessions. Frontend same origin.
POST /auth/register {email,password,display_name}; POST /auth/login {email,password}; POST /auth/logout; GET /auth/me -> user or 401. User {id,email,display_name,role}. GET /users/{id} -> channel.
GET /videos?q=&channel_id=&mine=false -> array of video. Video {id,title,description,owner_id,owner_name,visibility,status,hidden,views,likes,liked,created_at,duration,thumbnail_url}. statuses uploaded,processing,ready,failed. visibility public,unlisted,private.
POST /videos multipart file,title,description,visibility -> video (202); GET /videos/{id} -> video with comments via separate endpoint. PATCH /videos/{id} {title?,description?,visibility?}; DELETE /videos/{id}.
GET /videos/{id}/media supports Range, uses authorization; GET /videos/{id}/thumbnail likewise. POST /videos/{id}/view.
PUT /videos/{id}/like -> {liked,likes}; DELETE /videos/{id}/like.
GET /videos/{id}/comments -> [{id,body,user_id,display_name,created_at}]; POST same {body}; DELETE /comments/{id}.
PUT /videos/{id}/access {email}; DELETE /videos/{id}/access/{user_id}; GET /videos/{id}/access -> users (owner only).
PUT /users/{id}/subscribe; DELETE same; channel response {id,display_name,subscribers,subscribed}.
GET /playlists?channel_id= -> array {id,title,owner_id,videos:[]}; POST /playlists {title}; PUT /playlists/{id}/videos/{video_id}; DELETE same.
POST /videos/{id}/reports {reason}; GET /moderation/reports (staff); POST /moderation/videos/{id}/hide {hidden:bool}; PATCH /admin/users/{id}/role {role}; GET /admin/users.
GET /health/live; GET /health/ready.
All IDs integers. Inaccessible private/hidden/non-ready videos return 404 for unauthorized viewers. Owners may inspect processing/hidden metadata; hidden content is never playable. Private ACL independent of staff role.
