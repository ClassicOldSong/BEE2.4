"""Generate random quarter tiles, like in Destroyed or Retro maps."""
import random
from collections import defaultdict, namedtuple

import conditions
import srctools
import template_brush
import utils
import vbsp
import comp_consts as consts
import instanceLocs
from perlin import SimplexNoise
from srctools import Property, Vec_tuple, Vec, Entity, Side, UVAxis

COND_MOD_NAME = None

LOGGER = utils.getLogger(__name__, alias='cond.cutoutTile')

TEX_DEFAULT = [
    ('squarebeams', consts.Special.SQUAREBEAMS),
    ('ceilingwalls', consts.Special.BACKPANELS_CHEAP),
    ('floorbase', consts.Special.BACKPANELS),
    ('tile_glue', 'concrete/concrete_modular_floor001e'),
    ('clip', consts.Tools.PLAYER_CLIP),
]

# Materials set for the cutout tile
MATS = defaultdict(list)

# We want to force tiles with these overlay materials to appear!
FORCE_TILE_MATS = {
    mat
    for mat, key in
    vbsp.TEX_VALVE.items()
    if key.startswith('overlay.')
}

FORCE_LOCATIONS = set()

# The template used to seal sides open to the void.
FLOOR_TEMP_SIDE_WORLD = 'BEE2_CUTOUT_TILE_FLOOR_SIDE_WORLD'
FLOOR_TEMP_SIDE_DETAIL = 'BEE2_CUTOUT_TILE_FLOOR_SIDE_DETAIL'

# Template used to seal floor sections 'covered' by a block.
FLOOR_TEMP_PILLAR = 'BEE2_CUTOUT_TILE_FLOOR_PILLAR'

BEAM_ROT_PRECISION = 100  # How many DP to use for the random digits.

BorderPoints = namedtuple('BorderPoints', 'wall ceil rot')

FLOOR_DEPTH = 8  # Distance we drop the floor


@conditions.meta_cond(priority=-1000, only_once=False)
def find_indicator_panels(inst: Entity):
    """We need to locate indicator panels, so they aren't overwritten.
    """
    if inst['file'].casefold() not in instanceLocs.resolve('[indpan]'):
        return
    loc = Vec(0, 0, -64).rotate_by_str(inst['angles'])
    loc += Vec.from_str(inst['origin'])

    # Sometimes (light bridges etc) a sign will be halfway between
    # tiles, so in that case we need to force 2 tiles.
    loc_min = (loc - (15, 15, 0)) // 32 * 32  # type: Vec
    loc_max = (loc + (15, 15, 0)) // 32 * 32  # type: Vec
    loc_min += (16, 16, 0)
    loc_max += (16, 16, 0)
    FORCE_LOCATIONS.add(loc_min.as_tuple())
    FORCE_LOCATIONS.add(loc_max.as_tuple())


@conditions.make_result('CutOutTile')
def res_cutout_tile(res: Property):
    """Generate random quarter tiles, like in Destroyed or Retro maps.

    - "MarkerItem" is the instance to look for.
    - "TileSize" can be "2x2" or "4x4".
    - rotateMax is the amount of degrees to rotate squarebeam models.

    Materials:
    - "squarebeams" is the squarebeams variant to use.
    - "ceilingwalls" are the sides of the ceiling section.
    - "floorbase" is the texture under floor sections.
    - "tile_glue" is used on top of a thinner tile segment.
    - "clip" is the player_clip texture used over floor segments.
        (This allows customising the surfaceprop.)
    - "Floor4x4Black", "Ceil2x2White" and other combinations can be used to
       override the textures used.
    """
    item = instanceLocs.resolve(res['markeritem'])

    INST_LOCS = {}  # Map targetnames -> surface loc
    CEIL_IO = []  # Pairs of ceil inst corners to cut out.
    FLOOR_IO = []  # Pairs of floor inst corners to cut out.

    overlay_ids = {}  # When we replace brushes, we need to fix any overlays
    # on that surface.

    MATS.clear()
    floor_edges = []  # Values to pass to add_floor_sides() at the end

    sign_loc = set(FORCE_LOCATIONS)
    # If any signage is present in the map, we need to force tiles to
    # appear at that location!
    for over in conditions.VMF.by_class['info_overlay']:
        if (
                over['material'].casefold() in FORCE_TILE_MATS and
                # Only check floor/ceiling overlays
                over['basisnormal'] in ('0 0 1', '0 0 -1')
                ):
            loc = Vec.from_str(over['origin'])
            # Sometimes (light bridges etc) a sign will be halfway between
            # tiles, so in that case we need to force 2 tiles.
            loc_min = (loc - (15, 15, 0)) // 32 * 32  # type: Vec
            loc_max = (loc + (15, 15, 0)) // 32 * 32  # type: Vec
            loc_min += (16, 16, 0)
            loc_max += (16, 16, 0)
            FORCE_LOCATIONS.add(loc_min.as_tuple())
            FORCE_LOCATIONS.add(loc_max.as_tuple())

    SETTINGS = {
        'floor_chance': srctools.conv_int(
            res['floorChance', '100'], 100),
        'ceil_chance': srctools.conv_int(
            res['ceilingChance', '100'], 100),
        'floor_glue_chance': srctools.conv_int(
            res['floorGlueChance', '0']),
        'ceil_glue_chance': srctools.conv_int(
            res['ceilingGlueChance', '0']),

        'rotate_beams': int(srctools.conv_float(
            res['rotateMax', '0']) * BEAM_ROT_PRECISION),

        'beam_skin': res['squarebeamsSkin', '0'],

        'base_is_disp': srctools.conv_bool(res['dispBase', '0']),

        'quad_floor': res['FloorSize', '4x4'].casefold() == '2x2',
        'quad_ceil': res['CeilingSize', '4x4'].casefold() == '2x2',
    }

    random.seed(vbsp.MAP_RAND_SEED + '_CUTOUT_TILE_NOISE')
    noise = SimplexNoise(period=4 * 40)  # 4 tiles/block, 50 blocks max

    # We want to know the number of neighbouring tile cutouts before
    # placing tiles - blocks away from the sides generate fewer tiles.
    floor_neighbours = defaultdict(dict)  # all_floors[z][x,y] = count

    for mat_prop in res['Materials', []]:
        MATS[mat_prop.name].append(mat_prop.value)

    if SETTINGS['base_is_disp']:
        # We want the normal brushes to become nodraw.
        MATS['floorbase_disp'] = MATS['floorbase']
        MATS['floorbase'] = ['tools/toolsnodraw']

        # Since this uses random data for initialisation, the alpha and
        # regular will use slightly different patterns.
        alpha_noise = SimplexNoise(period=4 * 50)
    else:
        alpha_noise = None

    for key, default in TEX_DEFAULT:
        if key not in MATS:
            MATS[key] = [default]

    # Find our marker ents
    for inst in conditions.VMF.by_class['func_instance']: # type: VLib.Entity
        if inst['file'].casefold() not in item:
            continue
        targ = inst['targetname']
        orient = Vec(0, 0, 1).rotate_by_str(inst['angles', '0 0 0'])
        # Check the orientation of the marker to figure out what to generate
        if orient == (0, 0, 1):
            io_list = FLOOR_IO
        else:
            io_list = CEIL_IO

        # Reuse orient to calculate where the solid face will be.
        loc = (orient * -64) + Vec.from_str(inst['origin'])
        INST_LOCS[targ] = loc

        for out in inst.output_targets():
            io_list.append((targ, out))

        if not inst.outputs and inst.fixup['$connectioncount'] == '0':
            # If the item doesn't have any connections, 'connect'
            # it to itself so we'll generate a 128x128 tile segment.
            io_list.append((targ, targ))
        inst.remove()  # Remove the instance itself from the map.

    for start_floor, end_floor in FLOOR_IO:
        if end_floor not in INST_LOCS:
            # Not a marker - remove this and the antline.
            for toggle in conditions.VMF.by_target[end_floor]:
                conditions.remove_ant_toggle(toggle)
            continue

        box_min = Vec(INST_LOCS[start_floor])
        box_min.min(INST_LOCS[end_floor])

        box_max = Vec(INST_LOCS[start_floor])
        box_max.max(INST_LOCS[end_floor])

        if box_min.z != box_max.z:
            continue  # They're not in the same level!
        z = box_min.z

        if SETTINGS['rotate_beams']:
            # We have to generate 1 model per 64x64 block to do rotation...
            gen_rotated_squarebeams(
                box_min - (64, 64, 0),
                box_max + (64, 64, -8),
                skin=SETTINGS['beam_skin'],
                max_rot=SETTINGS['rotate_beams'],
            )
        else:
            # Make the squarebeams props, using big models if possible
            gen_squarebeams(
                box_min + (-64, -64, 0),
                box_max + (64, 64, -8),
                skin=SETTINGS['beam_skin']
            )

        # Add a player_clip brush across the whole area
        conditions.VMF.add_brush(conditions.VMF.make_prism(
            p1=box_min - (64, 64, FLOOR_DEPTH),
            p2=box_max + (64, 64, 0),
            mat=MATS['clip'][0],
        ).solid)

        # Add a noportal_volume covering the surface, in case there's
        # room for a portal.
        noportal_solid = conditions.VMF.make_prism(
            # Don't go all the way to the sides, so it doesn't affect wall
            # brushes.
            p1=box_min - (63, 63, 9),
            p2=box_max + (63, 63, 0),
            mat='tools/toolsinvisible',
        ).solid
        noportal_ent = conditions.VMF.create_ent(
            classname='func_noportal_volume',
            origin=box_min.join(' '),
        )
        noportal_ent.solids.append(noportal_solid)

        if SETTINGS['base_is_disp']:
            # Use displacements for the base instead.
            make_alpha_base(
                box_min + (-64, -64, 0),
                box_max + (64, 64, 0),
                noise=alpha_noise,
            )

        for x, y in utils.iter_grid(
                min_x=int(box_min.x),
                max_x=int(box_max.x) + 1,
                min_y=int(box_min.y),
                max_y=int(box_max.y) + 1,
                stride=128,
                ):
            # Build the set of all positions..
            floor_neighbours[z][x, y] = -1

        # Mark borders we need to fill in, and the angle (for func_instance)
        # The wall is the face pointing inwards towards the bottom brush,
        # and the ceil is the ceiling of the block above the bordering grid
        # points.
        for x in range(int(box_min.x), int(box_max.x) + 1, 128):
            # North
            floor_edges.append(BorderPoints(
                wall=Vec(x, box_max.y + 64, z - 64),
                ceil=Vec_tuple(x, box_max.y + 128, z),
                rot=270,
            ))
            # South
            floor_edges.append(BorderPoints(
                wall=Vec(x, box_min.y - 64, z - 64),
                ceil=Vec_tuple(x, box_min.y - 128, z),
                rot=90,
            ))

        for y in range(int(box_min.y), int(box_max.y) + 1, 128):
            # East
            floor_edges.append(BorderPoints(
                wall=Vec(box_max.x + 64, y, z - 64),
                ceil=Vec_tuple(box_max.x + 128, y, z),
                rot=180,
            ))

            # West
            floor_edges.append(BorderPoints(
                wall=Vec(box_min.x - 64, y, z - 64),
                ceil=Vec_tuple(box_min.x - 128, y, z),
                rot=0,
            ))

    # Now count boundries near tiles, then generate them.

    # Do it seperately for each z-level:
    for z, xy_dict in floor_neighbours.items():  # type: float, dict
        for x, y in xy_dict:  # type: float, float
            # We want to count where there aren't any tiles
            xy_dict[x, y] = (
                ((x - 128, y - 128) not in xy_dict) +
                ((x - 128, y + 128) not in xy_dict) +
                ((x + 128, y - 128) not in xy_dict) +
                ((x + 128, y + 128) not in xy_dict) +

                ((x - 128, y) not in xy_dict) +
                ((x + 128, y) not in xy_dict) +
                ((x, y - 128) not in xy_dict) +
                ((x, y + 128) not in xy_dict)
            )

        max_x = max_y = 0

        weights = {}
        # Now the counts are all correct, compute the weight to apply
        # for tiles.
        # Adding the neighbouring counts will make a 5x5 area needed to set
        # the center to 0.

        for (x, y), cur_count in xy_dict.items():
            max_x = max(x, max_x)
            max_y = max(y, max_y)

            # Orthrogonal is worth 0.2, diagonal is worth 0.1.
            # Not-present tiles would be 8 - the maximum
            tile_count = (
                0.8 * cur_count +
                0.1 * xy_dict.get((x - 128, y - 128), 8) +
                0.1 * xy_dict.get((x - 128, y + 128), 8) +
                0.1 * xy_dict.get((x + 128, y - 128), 8) +
                0.1 * xy_dict.get((x + 128, y + 128), 8) +

                0.2 * xy_dict.get((x - 128, y), 8) +
                0.2 * xy_dict.get((x, y - 128), 8) +
                0.2 * xy_dict.get((x, y + 128), 8) +
                0.2 * xy_dict.get((x + 128, y), 8)
            )
            # The number ranges from 0 (all tiles) to 12.8 (no tiles).
            # All tiles should still have a small chance to generate tiles.
            weights[x, y] = min((tile_count + 0.5) / 8, 1)

        # Share the detail entity among same-height tiles..
        detail_ent = conditions.VMF.create_ent(
            classname='func_detail',
        )

        for x, y in xy_dict:
            convert_floor(
                Vec(x, y, z),
                overlay_ids,
                MATS,
                SETTINGS,
                sign_loc,
                detail_ent,
                noise_weight=weights[x, y],
                noise_func=noise,
            )

    add_floor_sides(floor_edges)

    conditions.reallocate_overlays(overlay_ids)

    return conditions.RES_EXHAUSTED


def get_noise(loc: Vec, noise_func: SimplexNoise):
    """Generate a number between 0 and 1.

    This is used to determine where tiles are placed.
    """
    # Average between the neighbouring locations, to smooth out changes.
    return sum(
        # + 1 / 2 fixes the value range (originally -1,1 -> 0,1)
        (noise_func.noise3(loc.x + x, loc.y + y, loc.z) + 1) / 2
        for x in (-1, 0, 1)
        for y in (-1, 0, 1)
    ) / 9


def convert_floor(
        loc: Vec,
        overlay_ids,
        mats,
        settings,
        signage_loc,
        detail,
        noise_weight,
        noise_func: SimplexNoise,
):
    """Cut out tiles at the specified location."""
    try:
        brush = conditions.SOLIDS[loc.as_tuple()]
    except KeyError:
        return False  # No tile here!


    if brush.normal == (0, 0, 1):
        # This is a pillar block - there isn't actually tiles here!
        # We need to generate a squarebeams brush to fill this gap.

        brush.face.mat = 'tools/toolsnodraw'  # It won't be visible
        temp_data = template_brush.import_template(
            temp_name=FLOOR_TEMP_PILLAR,
            origin=loc,
        )
        template_brush.retexture_template(
            temp_data,
            loc,
            # Switch to use the configured squarebeams texture
            replace_tex={
                consts.Special.SQUAREBEAMS: random.choice(
                    MATS['squarebeams']
                ),
            }
        )
        return False

    # The new brush IDs overlays need to use
    # NOTE: strings, not ints!
    ant_locs = overlay_ids[str(brush.face.id)] = []

    # Move the floor brush down and switch to the floorbase texture.
    for plane in brush.face.planes:
        plane.z -= FLOOR_DEPTH
    brush.face.mat = random.choice(mats['floorbase'])

    loc.x -= 64
    loc.y -= 64

    for x, y in utils.iter_grid(max_x=4, max_y=4):
        tile_loc = loc + (x * 32 + 16, y * 32 + 16, 0)
        if tile_loc.as_tuple() in signage_loc:
            # Force the tile to be present under signage..
            should_make_tile = True
            rand = 100
            # We don't need to check this again in future!
            signage_loc.remove(tile_loc.as_tuple())
        else:
            # Create a number between 0-100
            rand = 100 * get_noise(tile_loc // 32, noise_func) + 10

            # Adjust based on the noise_weight value, so boundries have more tiles
            rand *= 0.1 + 0.9 * (1 - noise_weight)

            should_make_tile = rand < settings['floor_chance']
            if random.randint(0, 7) == 0:
                # Sometimes there'll be random holes/extra tiles
                should_make_tile = not should_make_tile

        if should_make_tile:
            # Full tile
            tile = make_tile(
                p1=tile_loc - (16, 16, 0),
                p2=tile_loc + (16, 16, -2),
                top_mat=vbsp.get_tex(str(brush.color) + '.floor'),
                bottom_mat='tools/toolsnodraw',
                beam_mat=random.choice(mats['squarebeams']),
            )
            detail.solids.append(tile.solid)
            ant_locs.append(str(tile.top.id))
        elif rand < settings['floor_glue_chance']:
            # 'Glue' tile - this chance should be higher, making these appear
            # bordering the full tiles.
            tile = make_tile(
                p1=tile_loc - (16, 16, 1),
                p2=tile_loc + (16, 16, -2),
                top_mat=random.choice(mats['tile_glue']),
                bottom_mat='tools/toolsnodraw',
                beam_mat=random.choice(mats['squarebeams']),
            )
            detail.solids.append(tile.solid)
        else:
            # No tile at this loc!
            pass

    return True


def make_tile(p1, p2, top_mat, bottom_mat, beam_mat):
    """Generate a 2 or 1 unit thick squarebeams tile.

    """
    prism = conditions.VMF.make_prism(p1, p2)
    brush, t, b, n, s, e, w = prism
    t.mat = top_mat
    b.mat = bottom_mat

    n.mat = beam_mat
    s.mat = beam_mat
    e.mat = beam_mat
    w.mat = beam_mat

    thickness = abs(p1.z - p2.z)

    if thickness == 2:
        # The z-axis texture offset needed
        # The texture is 512 high, so wrap around
        # 56 is the offset for the thin-line part of squarebeams
        # Textures are at 0.25 size, so 4 per hammer unit
        z_off = ((max(p1.z, p2.z) * 4) + 56) % 512
    elif thickness == 1:
        # Slightly different offset, so the line is still centered
        z_off = ((max(p1.z, p2.z) * 4) + 54) % 512
    else:
        raise ValueError(
            'Tile has incorrect thickness '
            '(expected 1 or 2, got {})'.format(thickness)
        )

    n.uaxis = UVAxis(
        0, 0, 1, offset=z_off)
    n.vaxis = UVAxis(
        1, 0, 0, offset=0)
    s.uaxis = n.uaxis.copy()
    s.vaxis = n.vaxis.copy()

    e.uaxis = UVAxis(
        0, 0, 1, offset=z_off)
    e.vaxis = UVAxis(
        0, 1, 0, offset=0)
    w.uaxis = e.uaxis.copy()
    w.vaxis = e.vaxis.copy()

    # Ensure the squarebeams textures aren't replaced, as well as floor tex
    vbsp.IGNORED_FACES.update(brush.sides)

    return prism


def _make_squarebeam(origin, skin='0', size=''):
    """Make a squarebeam prop at the given location."""
    return conditions.VMF.create_ent(
        classname='prop_static',
        angles='0 0 0',
        origin=origin.join(' '),
        model='models/anim_wp/framework/squarebeam_off' + size + '.mdl',
        skin=skin,
        disableshadows='1',
    )


def gen_rotated_squarebeams(p1: Vec, p2: Vec, skin, max_rot: int):
    """Generate broken/rotated squarebeams in a region.

    They will be rotated around their centers, not the model origin.
    """
    z = min(p1.z, p2.z) + 3  # The center of the beams
    for x, y in utils.iter_grid(
            min_x=int(p1.x),
            min_y=int(p1.y),
            max_x=int(p2.x),
            max_y=int(p2.y),
            stride=64):
        rand_x = random.randint(-max_rot, max_rot) / BEAM_ROT_PRECISION
        rand_z = random.randint(-max_rot, max_rot) / BEAM_ROT_PRECISION
        # Don't rotate around yaw - the vertical axis.

        # Squarebeams are offset 5 units from their real center
        offset = Vec(0, 0, 5).rotate(rand_x, 0, rand_z)
        prop = _make_squarebeam(Vec(x + 32, y + 32, z) + offset, skin=skin)
        prop['angles'] = '{} 0 {}'.format(rand_x, rand_z)


def gen_squarebeams(p1, p2, skin, gen_collision=True):
    """Generate squarebeams props to fill the space given.

    The space should be in multiples of 64. The squarebeams brush will
    be aligned to the lowest point in the space.
    """
    z = min(p1.z, p2.z) + 8
    min_x = min(p1.x, p2.x)
    min_y = min(p1.y, p2.y)
    max_x = max(p1.x, p2.x)
    max_y = max(p1.y, p2.y)

    dist_x = max_x - min_x
    dist_y = max_y - min_y

    # After this x or y dist, move to the next grid size.
    cutoff_512_x = dist_x // 512 * 512
    cutoff_256_x = dist_x // 256 * 256
    cutoff_128_x = dist_x // 128 * 128

    cutoff_512_y = dist_y // 512 * 512
    cutoff_256_y = dist_y // 256 * 256
    cutoff_128_y = dist_y // 128 * 128

    for x, y in utils.iter_grid(
            max_x=int(dist_x),
            max_y=int(dist_y),
            stride=64,
            ):
        if x < cutoff_512_x and y < cutoff_512_y:
            # Make 1 prop every 512 units, at the center
            if x % 512 == 0 and y % 512 == 0:
                _make_squarebeam(
                    Vec(min_x + x + 256, min_y +  y + 256, z),
                    skin, '_8x8',
                )
        elif x < cutoff_256_x and y < cutoff_256_y:
            if x % 256 == 0 and y % 256 == 0:
                _make_squarebeam(
                    Vec(min_x + x + 128, min_y + y + 128, z),
                    skin, '_4x4',
                )
        elif x < cutoff_128_x and y < cutoff_128_y:
            if x % 128 == 0 and y % 128 == 0:
                _make_squarebeam(
                    Vec(min_x + x + 64, min_y + y + 64, z),
                    skin, '_2x2',
                )
        else:
            # Make squarebeams for every point!
            _make_squarebeam(
                Vec(min_x + x + 32, min_y + y + 32, z),
                skin,
            )

    if gen_collision:
        collision = conditions.VMF.create_ent(
            classname='func_brush',
            disableshadows='1',
            disableflashlight='1',
            disablereceiveshadows='1',
            shadowdepthnocache='1',
            solidity='2',  # Always Solid
            solidbsp='1',
        )
        for x in range(int(min_x)+64, int(max_x), 64):
            collision.solids.append(
                conditions.VMF.make_prism(
                    p1=Vec(x-2, min_y+2, z-2),
                    p2=Vec(x+2, max_y-2, z-8),
                    mat='tools/toolsnodraw',
                ).solid
            )
        for y in range(int(min_y)+64, int(max_y), 64):
            collision.solids.append(
                conditions.VMF.make_prism(
                    p1=Vec(min_x+2, y-2, z-2),
                    p2=Vec(max_x-2, y+2, z-8),
                    mat='tools/toolsnodraw',
                ).solid
            )
        for x1, y1, x2, y2 in [
                (min_x, min_y, max_x, min_y+2),
                (min_x, max_y, max_x, max_y-2),
                (min_x, min_y, min_x+2, max_y),
                (max_x, min_y, max_x-2, max_y),
                ]:
            collision.solids.append(
                conditions.VMF.make_prism(
                    p1=Vec(x1, y1, z-2),
                    p2=Vec(x2, y2, z-8),
                    mat='tools/toolsnodraw',
                ).solid
            )


def make_alpha_base(bbox_min: Vec, bbox_max: Vec, noise: SimplexNoise):
    """Add the base to a CutoutTile, using displacements."""
    # We want to limit the size of brushes to 512, so the vertexes don't
    # get too far apart.
    # This now contains each point from beginning to end inclusive.
    x, y, z = bbox_min

    dim_x = bbox_max.x - bbox_min.x
    dim_y = bbox_max.y - bbox_min.y

    widths = [
        x
        for x in
        range(0, int(dim_x), 512)
    ] + [dim_x]
    heights = [
        y
        for y in
        range(0, int(dim_y), 512)
    ] + [dim_y]

    # Loop over two offset copies, so we get a min and max each time.
    for x1, x2 in zip(widths, widths[1:]):
        for y1, y2 in zip(heights, heights[1:]):
            # We place our displacement 1 unit above the surface, then offset
            # the verts down.
            brush = conditions.VMF.make_prism(
                Vec(x + x1, y + y1, z - FLOOR_DEPTH),
                Vec(x + x2, y + y2, z - FLOOR_DEPTH - 1),
            )
            brush.top.mat = random.choice(MATS['floorbase_disp'])
            make_displacement(
                brush.top,
                offset=-1,
                noise=noise,
            )
            conditions.VMF.add_brush(brush.solid)


def make_displacement(
        face: Side,
        noise: SimplexNoise,
        power=3,
        offset=0,
        ):
    """Convert the given face to a displacement with random paint alpha."""
    bbox_min, bbox_max = face.get_bbox()
    face.is_disp = True
    face.disp_elev = offset  # An overall +- to z.
    face.disp_flags = 0  # Leave it solid
    face.disp_is_subdiv = False
    face.disp_power = power
    face.disp_pos = bbox_min.copy()
    face.disp_allowed_verts = {
        '10': '-1 -1 -1 -1 -1 -1 -1 -1 -1 -1',
    }

    LOGGER.debug('Making displacement from {} to {}', bbox_min, bbox_max)

    # Number of rows/columns needed
    grid_size = 2 ** power + 1

    # The width/height of the vertextes - this ensures neighbouring
    # noise matches up correctly.
    x_vert = (bbox_max.x - bbox_min.x) / grid_size
    y_vert = (bbox_max.y - bbox_min.y) / grid_size

    face.disp_data = {
        # We just want these values repeated the right number of times!
        'normals': '0 0 1',
        'distances': '0',
        'offsets': '0',
        'offset_normals': '0 0 1',
        'triangle_tags': '9',  # Walkable
    }
    for key, val in face.disp_data.items():
        # We can duplicate immutable strings fine..
        face.disp_data[key] = [val * grid_size] * grid_size

    face.disp_data['alphas'] = [
        ' '.join(
            str(512 * get_noise(
                Vec(
                    bbox_min.x + x * x_vert,
                    bbox_min.y + y * y_vert,
                    bbox_min.z,
                ) // max(x_vert, y_vert),
                noise,
            ))
            for x in
            range(grid_size)
        )
        for y in range(grid_size)
    ]


def add_floor_sides(locs):
    """We need to replace nodraw textures around the outside of the holes.

    This requires looping through all faces, since these will have been
    nodrawed.
    """
    added_locations = {
        barrier.wall.as_tuple(): False
        for barrier in
        locs
    }

    for face in conditions.VMF.iter_wfaces(world=True, detail=False):
        if face.mat != 'tools/toolsnodraw':
            continue
        loc = face.get_origin().as_tuple()
        if loc in added_locations:
            random.seed('floor_side_{}_{}_{}'.format(*loc))
            face.mat = random.choice(MATS['squarebeams'])
            added_locations[loc] = True
            # Swap these to flip the texture diagonally, so the beam is at top
            face.uaxis, face.vaxis = face.vaxis, face.uaxis
            face.uaxis.offset = 48

            vbsp.IGNORED_FACES.add(face)

    # Look for the ones without a texture - these are open to the void and
    # need to be sealed. The template chamfers the edges
    # to prevent showing void at outside corners.
    for wall_loc, ceil_loc, rot in locs:
        if added_locations[wall_loc.as_tuple()]:
            continue

        diag_loc = (wall_loc.x, wall_loc.y, wall_loc.z + 128)

        temp_data = template_brush.import_template(
            # If there's a wall surface directly above this point
            # or a ceiling brush in the next block over
            # we want to use a world brush to seal the leak.
            # Otherwise we use the detail version for inside the map.
            temp_name=(
                FLOOR_TEMP_SIDE_DETAIL if
                ceil_loc not in conditions.SOLIDS and
                diag_loc not in conditions.SOLIDS
                else FLOOR_TEMP_SIDE_WORLD
            ),
            origin=wall_loc,
            angles=Vec(0, rot, 0),
        )
        template_brush.retexture_template(
            temp_data,
            wall_loc,
            # Switch to use the configured squarebeams texture
            replace_tex={
                consts.Special.SQUAREBEAMS: random.choice(
                    MATS['squarebeams']
                ),
            }
        )
