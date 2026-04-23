from pydantic import BaseModel, Field

class UploadStats(BaseModel):
    saved_new: int = Field(..., description="Number of new games added to the database")
    total_processed: int = Field(..., description="Total number of moves found in the PGN file")

class UploadResponse(BaseModel):
    message: str = Field(..., description="Transaction Status Notification")
    stats: UploadStats