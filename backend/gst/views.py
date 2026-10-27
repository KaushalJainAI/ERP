from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from django.utils.translation import gettext as _

from gst.validators import check_gstin


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def validate_gstin(request):
    """Cheap, stateless GSTIN check for inline form feedback.

    ``GET /api/gst/validate-gstin/?gstin=27AAACR5055K1Z7`` returns
    ``{valid, reason, state_code, state_name, pan}``. State code/name are returned
    whenever the prefix is a known state, even if the GSTIN is otherwise invalid;
    ``pan`` only when valid. Authenticated users only (it exposes no stored data).
    """
    raw = request.query_params.get("gstin")
    if raw is None or not raw.strip():
        raise ValidationError({"gstin": _("This query parameter is required.")})
    result = check_gstin(raw)
    return Response(
        {
            "valid": result.valid,
            "reason": result.reason,
            "state_code": result.state_code,
            "state_name": result.state_name,
            "pan": result.pan,
        }
    )
