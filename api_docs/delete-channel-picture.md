{generate_api_header(/streams/{stream_id}/icon:delete)}

## Parameters

{generate_api_arguments_table|zulip.yaml|/streams/{stream_id}/icon:delete}

## Response

```json
{"result": "success", "msg": ""}
```

## Example

```sh
curl --request DELETE https://zulip.example.com/api/v1/streams/7/icon \
    --user BOT_EMAIL:BOT_API_KEY
```
