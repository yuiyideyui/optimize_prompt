"""ComfyUI API and lightweight page for Comic2Anime asset management."""

import json


def _repository():
    from ..core.asset_repository import AssetRepository
    repo = AssetRepository()
    repo.initialize()
    return repo


def register_resource_routes():
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    app = PromptServer.instance.routes
    if getattr(register_resource_routes, "_registered", False):
        return

    async def resources(request):
        project_id = request.query.get("project_id", "").strip()
        if not project_id:
            raise web.HTTPBadRequest(text="project_id is required")
        repo = _repository()
        with repo.database.connect() as connection:
            chapters = [dict(row) for row in connection.execute(
                "SELECT * FROM chapters WHERE project_id = ? ORDER BY chapter_index, chapter_id",
                (project_id,),
            )]
            pages = [dict(row) for row in connection.execute(
                "SELECT * FROM pages WHERE project_id = ? ORDER BY chapter_id, page_index",
                (project_id,),
            )]
            assets = [dict(row) for row in connection.execute(
                "SELECT a.*, r.entity_type, r.entity_id, r.relation_type "
                "FROM assets a LEFT JOIN asset_relations r ON r.asset_id=a.asset_id "
                "WHERE a.project_id = ? ORDER BY a.created_at DESC",
                (project_id,),
            )]
        for asset in assets:
            try:
                asset["metadata"] = json.loads(asset.pop("metadata_json", "{}"))
            except json.JSONDecodeError:
                asset["metadata"] = {}
        page_by_hash = {page["sha256"]: page["page_id"] for page in pages}
        with repo.database.connect() as connection:
            provenance_rows = connection.execute(
                "SELECT o.asset_id, r.parameters_json FROM asset_generation_outputs o "
                "JOIN asset_generation_runs r ON r.run_id = o.run_id "
                "WHERE r.project_id = ? ORDER BY r.created_at DESC",
                (project_id,),
            ).fetchall()
        source_pages = {}
        for provenance in provenance_rows:
            try:
                parameters = json.loads(provenance["parameters_json"] or "{}")
            except json.JSONDecodeError:
                continue
            page_id = parameters.get("source_page_id") or page_by_hash.get(parameters.get("source_page_hash"))
            if page_id:
                source_pages.setdefault(provenance["asset_id"], page_id)
        for asset in assets:
            asset["metadata"].setdefault(
                "source_page_id", source_pages.get(asset["asset_id"])
            )
            asset["image_url"] = "/comic2anime/resources/{}/{}/image".format(
                project_id, asset["asset_id"]
            )
        return web.json_response({"project_id": project_id, "chapters": chapters, "pages": pages, "assets": assets})

    async def delete_asset(request):
        project_id = request.match_info["project_id"]
        asset_id = request.match_info["asset_id"]
        try:
            result = _repository().delete_asset(project_id, asset_id)
        except ValueError as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        return web.json_response({"deleted": True, "asset_id": result["asset_id"], "file_deleted": result["file_deleted"]})

    async def asset_image(request):
        project_id = request.match_info["project_id"]
        asset_id = request.match_info["asset_id"]
        repo = _repository()
        try:
            asset = repo.get_asset(asset_id, project_id)
        except ValueError as exc:
            raise web.HTTPNotFound(text=str(exc)) from exc
        path = repo.resolve_asset_path(asset)
        if not path.is_file():
            raise web.HTTPNotFound(text="Asset file is missing")
        return web.FileResponse(path)

    app.get("/comic2anime/resources")(resources)
    app.get("/comic2anime/resources/{project_id}/{asset_id}/image")(asset_image)
    app.delete("/comic2anime/resources/{project_id}/{asset_id}")(delete_asset)
    register_resource_routes._registered = True


register_resource_routes()
