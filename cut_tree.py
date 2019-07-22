#!/usr/bin/env python

import aiohttp
import logging
import re


ARXIV_ABS_URL_BASE = "https://arxiv.org/abs/"
ARXIV_PDF_URL_BASE = "https://arxiv.org/pdf/"
# poor man's XML parser; group the ID and text title separately
FIND_TITLE_REGEX = re.compile("<title>\[(\d*\.\d*)\](.*)</title>")
FIND_ID_REGEX = re.compile("\d{4}\.\d{5,6}") #lol, maybe one day we'd publish > 99999 articles per month

LOGGER_FORMAT = '%(asctime)s %(message)s'
logging.basicConfig(format=LOGGER_FORMAT, datefmt='[%H:%M:%S]')
log = logging.getLogger()
log.setLevel(logging.INFO)


async def get_pdf_file(session, aid):
    url = ARXIV_PDF_URL_BASE + aid
    async with session.get(url) as resp:
        log.debug("requesting PDF from {}".format(url))
        resp.raise_for_status()
        return await resp.read()


async def get_pdf_name(session, aid):
    url = ARXIV_ABS_URL_BASE + aid
    async with session.get(url) as resp:
        log.debug("requesting abstract from {}".format(url))
        resp.raise_for_status()
        xml = await resp.text()
        match = re.search(FIND_TITLE_REGEX, xml)
        return str(match.group(1)), str(match.group(2).strip())


def get_article_id(line):
    match = re.search(FIND_ID_REGEX, line)
    if match:
        return match.group(0)
    else:
        raise ValueError("invalid input {}".format(line))


async def process_one_article(session, article_uri):
    aid = get_article_id(article_uri)
    log.info("processing article {}".format(aid))
    parsed_aid, text_title = await get_pdf_name(session, aid)
    assert aid == parsed_aid, "id {} obtained from input does not match id {} from server".format(aid, parsed_aid)
    pdf_file = await get_pdf_file(session, aid)
    return pdf_file, text_title


async def main(article_list, postproc_fn=None):
    async with aiohttp.ClientSession() as session:
        for (aidx, task_result) in enumerate(asyncio.as_completed([process_one_article(session, a) for a in article_list])):
            pdf_file, title = await task_result
            log.info("Got file for article {}: {}".format(article_list[aidx], title))
            if postproc_fn:
                await postproc_fn(title, pdf_file)


if __name__ == "__main__":
    import argparse
    import asyncio
    import os
    parser = argparse.ArgumentParser(description=
            "Download articles from arXiv given the article id, optionally print and/or save the PDF")
    parser.add_argument("--save", type=str,default=None, help="location on disk to save the downloaded PDF;\
            if unspecified, program does not write to disk")
    parser.add_argument("--print", action='store_true', help="print with the default CUPS printer;\
            does not print if unspecified")
    parser.add_argument("--input", type=str, default=None, help="input specifying what article(s) to download.\
            Can be either a comma separated strings containing the article id(s) like YYMM.abcde,YYMM.xxxxx\
            or a file whose lines contain the article id")

    args = parser.parse_args()
    print_options = {}
    temp_dir = None
    def generate_article_list(input_str):
        if not input_str:
            raise ValueError("must supply input")
        ret = [token.strip() for token in input_str.split(',')]
        if (len(ret) == 0):
            raise ValueError("--input invalid; got {}".format(ret))
        elif (len(ret) == 1 and os.path.isfile(ret[0])):
            with open(ret,'r') as f:
                ret = [line.rstrip('\n') for line in f]
        return ret


    def setup_printing(should_print):
        if not should_print:
            log.info("not printing")
            return None
        ret = None
        try:
            import cups
            conn = cups.Connection()
            printers = conn.getPrinters()
            if not printers:
                raise RuntimeError("you don't have any printers setup with cups")
            name = conn.getDefault()
            loc = printers[name].get("printer-location", "unknown")
            logging.info("using printer {} at location {}".format(name, loc))
            def print_one_file(filepath, job_title, print_opt):
                job_id = conn.printFile(name, filepath, job_title, print_opt)
                log.info("submitting job {} ({}) to printer".format(job_id, job_title))
            ret = print_one_file 
        except ImportError as _:
            log.info("could not import pycups; make sure you have it installed with `pip install pycups")
        except Exception as e:
            print(e)
            log.info("not printing")
        finally:
            return ret 


    def setup_saving(save_dir, print_fn):
        if not save_dir and not print_fn:
            log.info("not saving")
            return None
        import aiofiles, os
        if save_dir:
            # setup saving
            if not os.path.exists(save_dir):
                os.mkdir(save_dir)
        elif print_fn:
            # not saving but printing, so need tempfile
            import tempfile
            save_dir = tempfile.mkdtemp()
            global temp_dir
            temp_dir = save_dir
        log.info("saving to {}".format(save_dir))


        async def save_file_fn(filename, bin_content):
            final_path = os.path.join(save_dir, filename)
            log.info("saving {}".format(final_path))
            async with aiofiles.open(final_path, 'wb') as pdf_file:
                await pdf_file.write(bin_content)
            return final_path


        return save_file_fn
 

    try:
        print_fn = setup_printing(args.print)
        save_fn = setup_saving(args.save, print_fn)
        async def postprocess_fn(title, pdf_content):
            if save_fn:
                filepath = await save_fn(title + ".pdf", pdf_content)
                if print_fn:
                    print_fn(filepath, title, print_options) 

        articles = generate_article_list(args.input)
        log.info("given {} as input".format(",".join(articles))) 
        asyncio.run(main(articles, postprocess_fn))
    finally:
        if temp_dir:
            log.info("deleting temporary directory")
            import shutil
            shutil.rmtree(temp_dir)

