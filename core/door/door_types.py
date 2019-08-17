import bmesh
from bmesh.types import BMEdge

from ..fill import fill_panel, fill_glass_panes, fill_louver
from ...utils import (
    arc_edge,
    select,
    filter_geom,
    face_with_verts,
    calc_edge_median,
    calc_face_dimensions,
    filter_vertical_edges,
    filter_horizontal_edges,
    inset_face_with_scale_offset,
    subdivide_face_edges_vertical,
)


def create_door(bm, faces, prop):
    """Create door from face selection
    """
    for face in faces:
        array_faces = create_door_array(bm, face, prop.array)

        for aface in array_faces:
            face = create_door_split(bm, aface, prop.size_offset)
            # -- check that split was successful
            if not face:
                continue

            face = create_door_frame(bm, face, prop)
            create_door_fill(bm, face, prop)


def create_door_split(bm, face, prop):
    """Use properties from SplitOffset to subdivide face into regular quads
    """
    size, off = prop.size, prop.offset
    return inset_face_with_scale_offset(bm, face, size.y, size.x, off.x, off.y, off.z)


def create_door_array(bm, face, prop):
    """Use ArrayProperty to subdivide face horizontally/vertically for further processing
    """
    if prop.count <= 1:
        return [face]
    res = subdivide_face_edges_vertical(bm, face, prop.count - 1)
    inner_edges = filter_geom(res["geom_inner"], bmesh.types.BMEdge)
    return list({f for e in inner_edges for f in e.link_faces})


def create_door_frame(bm, face, prop):
    """Extrude and inset face to make door frame
    """

    # -- dissolve bottom edge
    bottom = sorted([e for e in face.edges], key=lambda ed: calc_edge_median(ed).z)
    bmesh.ops.dissolve_edges(bm, edges=bottom[:1], use_verts=True)
    face = [f for f in bm.faces if f.index == len(bm.faces)-1][-1]

    # -- create arch
    top = sorted([e for e in face.edges], key=lambda ed: calc_edge_median(ed).z)[-1]

    normal = face.normal
    if prop.frame_thickness > 0:
        edges = filter_vertical_edges(face.edges, normal)
        top_edge = split_edges_horizontal_offset_top(bm, edges, prop.frame_thickness)
        face = min(top_edge.link_faces, key=lambda f: f.calc_center_median().z)

        w = calc_face_dimensions(face)[0]
        off = (w / 3) - prop.frame_thickness
        edges = split_face_vertical_with_offset(bm, face, 2, [off, off])

        face = list(set(edges[0].link_faces) & set(edges[1].link_faces))[-1]
        top2 = sorted([e for e in face.edges], key=lambda ed: calc_edge_median(ed).z)[-1]
        verts = list({vert
                      for v in top2.verts
                      for e in v.link_edges
                      for vert in e.verts if vert not in top2.verts})
        top_verts = sorted(verts, key=lambda v : v.co.z)[2:]
        for vert in top_verts:
            upper_link = sorted([v for e in vert.link_edges
                                for v in e.verts if v is not vert],
                                key=lambda v : v.co.z)[-1]
            bmesh.ops.pointmerge(bm, verts=[upper_link, vert], merge_co=upper_link.co)

    for e in [top, top2]:
        arc_edge(bm, e, prop.arch.resolution, prop.arch.height, prop.arch.offset)

    if prop.door_depth > 0.0:
        face = bmesh.ops.extrude_discrete_faces(bm, faces=[face]).get("faces")[-1]
        bmesh.ops.translate(bm, verts=face.verts, vec=-normal * prop.door_depth)

    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    if prop.frame_depth:
        f = extrude_face_and_delete_bottom(bm, face, -prop.frame_depth)
        return f
    return face


def create_door_fill(bm, face, prop):
    """Add decorative elements on door face
    """
    if prop.fill_type == "PANELS":
        fill_panel(bm, face, prop.panel_fill)
    elif prop.fill_type == "GLASS PANES":
        fill_glass_panes(bm, face, prop.glass_fill)
    elif prop.fill_type == "LOUVER":
        fill_louver(bm, face, prop.louver_fill)


def common_linked_top_edge(bm, edges):
    top_verts = sorted([v for e in edges for v in e.verts],
                       key=lambda v : v.co.z)[len(edges):]
    return bm.edges.get(top_verts)


def delete_bottom_face(bm, face):
    """delete the face that is at the bottom an extruded face
    """
    bottom_edge = min(
        filter_horizontal_edges(face.edges, face.normal),
        key=lambda e: calc_edge_median(e).z,
    )
    hidden = min(
        [f for f in bottom_edge.link_faces], key=lambda f: f.calc_center_median().z
    )
    bmesh.ops.delete(bm, geom=[hidden], context="FACES")


def extrude_face_and_delete_bottom(bm, face, extrude_depth):
    """extrude a face and delete bottom face from new geometry
    """
    f = bmesh.ops.extrude_discrete_faces(bm, faces=[face]).get("faces")[-1]
    bmesh.ops.translate(bm, verts=f.verts, vec=f.normal * extrude_depth)
    delete_bottom_face(bm, f)
    return f


def split_face_vertical_with_offset(bm, face, cuts, offsets):
    """split a face(quad) vertically and move the new edges
    """
    median = face.calc_center_median()
    res = subdivide_face_edges_vertical(bm, face, cuts)
    edges = filter_geom(res["geom_inner"], BMEdge)
    edges.sort(
        key=lambda e: getattr(calc_edge_median(e), "x" if face.normal.y else "y")
    )

    for off, e in zip(offsets, edges):
        tvec = calc_edge_median(e) - median
        bmesh.ops.translate(bm, verts=e.verts, vec=tvec.normalized() * off)
    return edges


def split_edges_horizontal_offset_top(bm, edges, offset):
    """split a face(quad) horizontally and move the new edge
    """
    face = face_with_verts(bm, list({v for e in edges for v in e.verts}))
    v_edges = filter_vertical_edges(face.edges, face.normal)
    new_verts = []
    for e in v_edges:
        vert = max(list(e.verts), key=lambda v: v.co.z)
        v = bmesh.utils.edge_split(e, vert, offset / e.calc_length())[-1]
        new_verts.append(v)

    res = bmesh.ops.connect_verts(bm, verts=new_verts).get("edges")
    return res[-1]
