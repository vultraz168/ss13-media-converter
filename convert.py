'''
python convert.py

MIT license

REQUIRES:
  sudo apt-get install -y sox libsox-fmt-all libav-tools
  sudo -H pip install mutagen pyyaml
'''
import glob
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time

from ruamel.yaml import YAML
from buildtools import os_utils, log
from functools import reduce

# Ryetalin is our shitty standardization library.
sys.path.append('lib/ryetalin')
import ryetalin #isort: skip

# Set up yaml
yaml = YAML(typ='rt')

OUTPUT_DIR = os.path.abspath('files-upl')
OUTPUT_EXT = 'mp3'  # WAS MP3

TIME_SOURCE = time.process_time
USE_FFMPEG = False # Changed by

SOX_ARGS = []
SOX_ARGS += 'silence 1 0.1 0.1% reverse silence 1 0.1 0.1% reverse'.split(' ')  # Remove silence
SOX_ARGS += 'norm'.split(' ')  # Normalize volume
#SOX_ARGS += 'rate 48k'.split(' ')  # downsample

# This changes the music to a 441k MP3, which is great for intertubes and doesn't break WMP.
cmds = [
    (
        'normalize, desilence',
        [
            'sox',
            #'-r',
            #'441k',  # Was 441k
            '{INFILE}',
            #'/tmp/sox-out.wav' #
            '{OUTFILE}'
        ] + SOX_ARGS,
        # '/tmp/sox-out.wav')
        '{OUTFILE}')
]
'''
cmds += [(
    'encode',
    [
    'avconv',
    '-i','/tmp/sox-out.wav',
    #'-bsf:a','aac_adtstoasc',
    #'-c:a', 'libmp3lame',
    '-maxrate:a', '441K', # 441K bitrate
    #'-b','441k',
    '-vn', # Drop video
    '-y',
    #'-strict','experimental',
    '{OUTFILE}'],
    '{OUTFILE}')
]
'''
# Was originally going to use OGG, but they ended up being large
#cmds += [('oggenc tmp/VOX-sox-word.wav -o '+oggfile,oggfile)]

PLAYLISTS = []

sourcefiles = []
deadfiles = []

fileRegistry = {}
config = {}
playlistIncludes = {}
md5s = {}
revmd5s = {}

FILECOUNT=0
FILEPROGRESS=0

source_dir = 'source'  # 'source_test'


def secondsToStr(t):
    return "%d:%02d:%02d.%03d" % \
        reduce(lambda ll, b: divmod(ll[0], b) + ll[1:],
               [(t * 1000,), 1000, 60, 60])

# Try to clean up UTF8 (NEVER WORKS)
def removeNonAscii(s):
    return ''.join(i for i in s if ord(i) < 128)

# Remove ampersands, hashes, question marks, and exclamation marks.
def cleanFilename(s):
    s = s.replace('&', 'and')
    nwfn = (i for i in s if i not in u"#?!")
    return ''.join(nwfn)


def move(fromdir, todir, fromexts, toext, op, **op_args):
    for root, _, files in os.walk(fromdir):
        #path = root.split('/')
        newroot = os.path.join(todir, root[len(fromdir) + 1:])
        for file in files:
            fromfile = os.path.join(root, file)
            _, ext = os.path.splitext(os.path.basename(fromfile))
            if ext.strip('.') not in fromexts:
                #logging.warning('Skipping {} ({})'.format(fromfile, ext))
                continue
            if not os.path.isdir(newroot):
                os.makedirs(newroot)
            op(fromfile, newroot, op_args)


def check(wdir, exts, op, **kwargs):
    for root, _, files in os.walk(wdir):
        for file in files:
            fromfile = os.path.join(root, file)
            _, ext = os.path.splitext(os.path.basename(fromfile))
            if len(exts) > 0 and ext.strip('.') not in exts:
                #logging.warning('Skipping {} ({})'.format(fromfile, ext))
                continue
            op(fromfile, **kwargs)


def md5_file_h(f, block_size=2**20):
    md5 = hashlib.md5()
    while True:
        data = f.read(block_size)
        if not data:
            break
        md5.update(data)
    return md5.hexdigest()


def md5_file(infile):
    md5 = ''
    with open(infile, 'rb') as f:
        md5 = md5_file_h(f).upper()
    return md5
md5s = {}


def filename_md5(infile, newroot, args):
    global md5s, revmd5s, config
    md5 = md5_file(infile)
    playlist = args['playlist']
    _, ext = os.path.splitext(os.path.basename(infile))
    a = md5[0]
    b = md5[1]
    subf = os.path.join(newroot, a, b)
    if not os.path.isdir(subf):
        os.makedirs(subf)
    tofile = os.path.join(subf, '{0}{1}'.format(md5[2:], ext))

    md5s[md5] = infile
    revmd5s[infile] = md5

    meta = ryetalin.Open(infile)
    regEntry = {}

    if md5 not in fileRegistry:
        if meta.tags is not None:
            for tagName in ['title', 'album', 'artist', 'tracknumber']:
                if tagName in meta.tags:
                    regEntry[tagName] = meta.tags[tagName]
        regEntry['playtime_seconds'] = str(meta.getLength())
        regEntry['playlists'] = [playlist]
        regEntry['orig_filename'] = os.path.basename(infile)
        fileRegistry[md5] = regEntry
    else:
        fileRegistry[md5]['playlists'] += [playlist]

    if os.path.isfile(tofile):
        #logging.warning('Skipping {} (exists)'.format(tofile))
        return
    shutil.copy(infile, tofile)
    logging.info('%s = %s', tofile, infile)


def straight_copy(infile, newroot, args):
    sinfile = removeNonAscii(infile)
    if sinfile != infile:
        logging.critical("File %s (stripped) has nonascii filename.", sinfile)
        sys.exit()
    base = os.path.basename(infile)
    title, ext = os.path.splitext(base)
    tofile = os.path.join(newroot, cleanFilename(title) + ext)
    if os.path.isfile(tofile):
        if md5_file(infile) == md5_file(tofile):
            #logging.warning('Skipping {} (exists)'.format(tofile))
            return
    shutil.copy(infile, tofile)


def check_for_dead_files(infile, basedir=OUTPUT_DIR):
    sinfile = removeNonAscii(infile)
    if sinfile != infile:
        logging.critical("File %s (stripped) has nonascii filename.", sinfile)
        sys.exit()
    global sourcefiles, deadfiles

    if sys.version_info[0] < 3:
        if isinstance(infile, unicode):
            infile = infile.decode('utf-8')
    else:
        if isinstance(infile, bytes):
            infile = infile.decode('utf-8')
    title, _ = os.path.splitext(os.path.basename(infile))
    playlist = os.path.relpath(os.path.dirname(infile), './' + basedir)
    sourcefile = os.path.join(playlist, cleanFilename(title))
    if sourcefile not in sourcefiles:
        deadfiles += [infile]


def check_md5s(infile):
    global md5s, deadfiles

    pathchunks = infile.split(os.sep)[-3:]
    title, _ = os.path.splitext(os.path.basename(infile))
    md5 = pathchunks[0] + pathchunks[1] + title
    if md5 not in md5s:
        deadfiles += [infile]

def check_count(_):
    global FILECOUNT
    FILECOUNT+=1


# Copy the file's tags and convert to id3v2
def update_tags(origf, newf):
    orig = ryetalin.Open(origf)
    if orig is None:
        logging.critical('Unable to open {}!'.format(origf))
        sys.exit(1)

    new = ryetalin.Open(newf)
    if new is None:
        logging.critical('Unable to open {}!'.format(newf))
        sys.exit()

    if 'artist' not in orig and 'album' not in orig and 'title' not in orig and 'tracknumber' not in orig:
        logging.warning('Tags not set on: {0}'.format(origf))
        # sys.exit(1)

    # you can iterate over the tag names
    # they will be the same for all file types
    changed = []
    numtags = 0
    for tag_name, tag_value in orig.tags.items():
        numtags += 1
        if 'albumartist' == tag_name:
            tag_name = 'artist'
        if tag_name not in ('artist', 'title', 'album', 'tracknumber'):
            #logging.info("Skipping {0} (invalid)",tag_name)
            continue
        orig_val = orig.tags[tag_name]
        if orig_val in ('0', '', 0):
            continue
        if tag_name not in new.tags or new.tags[tag_name] != orig.tags[tag_name]:
            new.tags[tag_name] = orig.tags[tag_name]
            #logging.info(u"{0}: {1} = {2}".format(newf,tag_name,orig_val))
            changed += [tag_name]
    #logging.info("{0}: {1} tags.".format(newf,numtags))
    warnings = []
    if 'title' not in new:
        warnings += ['title']
    if 'tracknumber' not in new:
        warnings += ['tracknumber']
    if 'artist' not in new and 'album' not in new:
        warnings += ['artist OR album']
    if len(warnings) > 0:
        logging.warning('Missing tags in {}: {}'.format(origf, ', '.join(warnings)))
    if len(changed) > 0:
        new.save()
        logging.info('Updated tags in {}: {}'.format(newf, ', '.join(changed)))


class TimeExecution(object):
    def __init__(self, label):
        self.start_time = None
        self.label = label

    def __enter__(self):
        self.start_time = TIME_SOURCE()
        return self

    def __exit__(self, type, value, traceback):
        logging.info('  Completed in %(diff)ss - %(label)s', {'label': self.label, 'diff': secondsToStr(TIME_SOURCE() - self.start_time)})
        return False


def check_converted(infile, basedir='tmp_files'):
    global sourcefiles
    sinfile = removeNonAscii(infile)
    if sinfile != infile:
        logging.critical("File {} (stripped) has nonascii filename.".format(sinfile))
        sys.exit()
    global sourcefiles, deadfiles
    if sys.version_info[0] < 3:
        if isinstance(infile, unicode):
            infile = infile.decode('utf-8')
    else:
        if isinstance(infile, bytes):
            infile = infile.decode('utf-8')
    title, ext = os.path.splitext(os.path.basename(infile))
    playlist = os.path.relpath(os.path.dirname(infile), './' + basedir)
    sourcefile = os.path.join(playlist, cleanFilename(title))
    if sourcefile not in sourcefiles:
        if os.path.isfile(infile):
            logging.critical("File {} has no matching source, removing.".format(infile))
            os.remove(infile)


def convert_to_mp3(infile, newroot, args):
    global sourcefiles, OUTPUT_EXT
    skip_tagging = args.get('skip_tagging', [])
    origfile = infile
    title, ext = os.path.splitext(os.path.basename(infile))
    playlist = os.path.relpath(os.path.dirname(infile), './' + source_dir)
    sfEntry = os.path.join(playlist, cleanFilename(title))
    sourcefiles += [sfEntry]
    outfile = os.path.join(newroot, '{0}{1}'.format(cleanFilename(title), '.' + OUTPUT_EXT))
    outdir = os.path.dirname(outfile)
    pct=(float(len(sourcefiles))/float(FILECOUNT))*100

    if not os.path.isdir(outdir):
        logging.info('Created %s', outdir)
        os.makedirs(outdir)

    if os.path.isfile(outfile):
        #logging.warning('Skipping {} (exists)'.format(outfile))
        if newroot not in skip_tagging:
            update_tags(origfile, outfile)
        return
    logging.info("[%d%%] Converting %s... (%d/%d)",pct,origfile,len(sourcefiles),FILECOUNT)

    start = 0
    if ext in ('.m4a', '.webm', '.ogg'):
        newfrom = './temp.wav'
        with TimeExecution('decode'):
            if not USE_FFMPEG:
                os_utils.cmd(['avconv', '-y', '-vn', '-i', infile, newfrom], echo=False, critical=True, show_output=False)
            else:
                # ffmpeg -i input.wav -codec:a libmp3lame -qscale:a 2 output.mp3
                os_utils.cmd(['ffmpeg', '-i', infile, '-maxrate:a', '441K', '-vn', '-y', newfrom], echo=False, critical=True, show_output=False)

        infile = newfrom
    for command_spec in cmds:
        (step_name, command, cfn) = command_spec
        newcmd = []
        for chunk in command:
            newcmd += [chunk.replace('{INFILE}', infile).replace('{OUTFILE}', outfile)]
        cfn = cfn.replace('{INFILE}', infile).replace('{OUTFILE}', outfile)
        with TimeExecution(step_name):
            if not os_utils.cmd(newcmd, echo=False, critical=True, show_output=True):
                logging.error(u"Command '{}' failed!".format(' '.join(newcmd)))
                if os.path.isfile(cfn):
                    logging.warning('Removed {0}.'.format(cfn))
                    os.remove(cfn)
                sys.exit(1)
        if not os.path.isfile(cfn):
            logging.error(u"File '{0}' doesn't exist, command '{1}' probably failed!".format(cfn, ' '.join(newcmd)))
            sys.exit(1)
    if newroot not in skip_tagging:
        update_tags(origfile, outfile)
    #logging.info('Created {0}'.format(outfile))


def cmd(command, echo=False):
    if echo:
        logging.info('>>> ' + repr(command))
    output = ''
    try:
        # if subprocess.call(command,shell=True) != 0:
        output = subprocess.check_output(command, shell=False, stderr=subprocess.STDOUT)
        return True
    except subprocess.CalledProcessError as e:
        logging.error(u"$ " + ' '.join(command))
        logging.error(e.output)
        logging.error('RETURN CODE: {0}'.format(e.returncode))
        return False


def del_dirs(src_dir):
    for dirpath, dirnames, filenames in os.walk(src_dir, topdown=False):  # Listing the files
        if dirpath == src_dir:
            break
        if len(filenames) == 0 and len(dirnames) == 0:
            logging.info('Removing %s (empty)', dirpath)
            os.rmdir(dirpath)


def main():
    import argparse
    global USE_FFMPEG

    argp = argparse.ArgumentParser()
    argp.add_argument('--use-avconv', dest='use_avconv', action='store_true', default=False, help='Use avconv for converting audio.  May work better on Debian.')
    argp.add_argument('--use-ffmpeg', dest='use_ffmpeg', action='store_true', default=False, help='Use ffmpeg for converting audio.')

    args = argp.parse_args()

    logging.basicConfig(format='%(asctime)s [%(levelname)-8s]: %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', level=logging.INFO)

    USE_FFMPEG=None
    if args.use_ffmpeg:
        logging.info('--use-ffmpeg is set, using ffmpeg.')
        USE_FFMPEG = True
    elif args.use_avconv:
        logging.info('--use-avconv is set, using avconv.')
        USE_FFMPEG = False
    else:
        with log.info('Autodetecting conversion software (neither --use-ffmpeg nor --use-avconv are set)...'):
            ffmpeg_path = os_utils.which('ffmpeg')
            if ffmpeg_path is not None:
                log.info('We\'ll use ffmpeg.')
                USE_FFMPEG = True
            else:
                avconv_path = os_utils.which('avconv')
                if avconv_path is not None:
                    log.info('We\'ll use avconv.')
                    USE_FFMPEG = False

    os_utils.assertWhich('sox')
    output = ''
    if USE_FFMPEG:
        os_utils.assertWhich('ffmpeg')
        output = os_utils.cmd_out(['ffmpeg', '-encoders'])
    else:
        os_utils.assertWhich('avconv')
        output = os_utils.cmd_out(['avconv', '-encoders'])
    haslame = False
    for line in output.splitlines():
        if 'libmp3lame' in line:
            haslame = True

    if not haslame:
        logging.critical('%s does not have access to libmp3lame for encoding MP3s.  Please install whatever codec pack your distro needs for LAME support.', 'ffmpeg' if USE_FFMPEG else 'avconv')
        sys.exit(1)

    with open('config.yml', 'r') as f:
        config = yaml.load(f)
        # print(repr(config))

    PLAYLISTS = list(config['playlists'].keys())

    check('source',('m4a', 'wav', 'mp3', 'ogg', 'webm', 'oga'),check_count)
    move(source_dir,
         'tmp_files',
         ('m4a', 'wav', 'mp3', 'ogg', 'webm', 'oga'),
         OUTPUT_EXT,
         convert_to_mp3,
         # skip_tagging=[os.path.join('tmp_files','emagged')]
         )

    logging.info('Checking converted files...')
    check('tmp_files', (OUTPUT_EXT,), check_converted)

    for playlist in PLAYLISTS:
        move(os.path.join('tmp_files', playlist), os.path.join(OUTPUT_DIR), [OUTPUT_EXT], OUTPUT_EXT, filename_md5, playlist=playlist)
    check(OUTPUT_DIR, ('m4a', 'wav', 'mp3', 'ogg', 'webm', 'oga'), check_md5s)

    for deadfile in deadfiles:
        if os.path.isfile(deadfile):
            logging.warning(f'Removing {deadfile} (outdated)')
            os.remove(deadfile)
        if os.path.isfile(deadfile):
            logging.critical('Failed to remove {deadfile}!')
    del_dirs(OUTPUT_DIR)

    for plID, plConfig in config['playlists'].items():
        numIncluded = 0
        if 'include' in plConfig:
            if 'files' in plConfig['include']:
                for nf in plConfig['include']['files']:
                    pathkey = os.path.join('tmp_files', nf)
                    if pathkey in revmd5s:
                        md5 = revmd5s[pathkey]
                        if plID not in fileRegistry[md5]['playlists']:
                            #logging.info('Media file %s included to playlist %s.',nf,plID)
                            fileRegistry[md5]['playlists'] += [plID]
                            numIncluded += 1
                        else:
                            logging.warning('Playlist %s trying to include %s, when it already has it.', plID, nf)
            if 'playlists' in plConfig['include']:
                playlistsWanted = plConfig['include']['playlists']
                if len(playlistsWanted) > 0:
                    for md5 in fileRegistry.keys():
                        wanted = False
                        for plWanted in playlistsWanted:
                            if plWanted in fileRegistry[md5]['playlists']:
                                wanted = True
                        if wanted and plID not in fileRegistry[md5]['playlists']:
                            #logging.info('Media file %s included to playlist %s.',md5,plID)
                            fileRegistry[md5]['playlists'] += [plID]
                            numIncluded += 1
        if numIncluded > 0:
            logging.info('%d media files included to playlist %s.', numIncluded, plID)

    # with open(OUTPUT_DIR+'/sauce.txt','w') as f:
    #    for md5,filename in sorted(md5s.items()):
    #        f.write("{0} {1}\r\n".format(md5,os.path.basename(filename)))

    with open(OUTPUT_DIR + '/fileData.json', 'w') as f:
        json.dump(fileRegistry, f, sort_keys=True, separators=(',', ':'), indent=4)

if __name__ == '__main__':
    main()
