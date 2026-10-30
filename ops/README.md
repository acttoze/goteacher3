# ops — 배포·운영 (gt-ops)

스테이징 `https://t.goteacher.xyz` = 서버 `oci_tailscale`의 `/home/ubuntu/prod/goteacher` (이 저장소 clone).

- `compose.yml` — compose 프로젝트 `goteacher`: `goteacher-app`(gunicorn) · `db`(PostgreSQL 17) · `redis`. 작업 큐(RQ worker)는 D3/E2에서 추가.
- `Dockerfile` — 빌드 컨텍스트는 저장소 루트(`.dockerignore`).
- `.env.example` — 서버 `ops/.env`(chmod 600) 견본. 실제 값은 서버에서 생성하고 저장소·출력에 남기지 않는다.

배포(서버에서):

```sh
cd /home/ubuntu/prod/goteacher && git pull --ff-only
cd ops && docker compose up -d --build
curl -I https://t.goteacher.xyz
```

공용 서버 규칙: 이 프로젝트 밖의 컨테이너·네트워크·볼륨과 Traefik 설정은 건드리지 않는다.
`docker system prune`·`volume prune` 금지. DB·Redis 포트는 호스트에 열지 않는다.
