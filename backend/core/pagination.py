from rest_framework.pagination import PageNumberPagination


class StablePageNumberPagination(PageNumberPagination):
    """
    PageNumberPagination that guarantees deterministic paging.

    Django/DRF paginate an *unordered* queryset in whatever order the database
    happens to return rows, so the same row can be skipped or repeated across
    pages (DRF warns: UnorderedObjectListWarning). Most ERP list endpoints don't
    declare a default ``ordering``, so this enforces a stable tie-breaker on the
    primary key. Any explicit ordering (OrderingFilter, model Meta.ordering,
    ``.order_by()``) already marks the queryset ordered and is left untouched.
    """

    def paginate_queryset(self, queryset, request, view=None):
        if hasattr(queryset, "ordered") and not queryset.ordered:
            queryset = queryset.order_by("pk")
        return super().paginate_queryset(queryset, request, view=view)
