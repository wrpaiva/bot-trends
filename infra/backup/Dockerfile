# infra/backup/Dockerfile — imagem do serviço `backup` (TIE-35)
# mongo:7 traz mongodump/mongosh; o rclone (binário estático) leva o dump para
# fora da máquina (Cloudflare R2 ou qualquer remoto S3). Versão fixa: o
# script depende da saída de `rclone lsf`.
FROM rclone/rclone:1.71.1 AS rclone

FROM mongo:7
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
