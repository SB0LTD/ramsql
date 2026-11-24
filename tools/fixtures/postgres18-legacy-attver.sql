--
-- PostgreSQL database dump
--

\restrict 4yt9rQJlfkkhceccLr8fgSW5yuNT8cfeLl0W52dA8k0k73y1mRdhisHjX4yC2oh

-- Dumped from database version 18.7 (Ubuntu 18.7-1.pgdg24.04+5)
-- Dumped by pg_dump version 18.0

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = "heap";

--
-- Name: legacy_attver; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE "public"."legacy_attver" (
    "id" integer NOT NULL,
    "Payload" "text" NOT NULL
);
ALTER TABLE ONLY "public"."legacy_attver" ALTER COLUMN "id" SET (pg_attver=29111072);
ALTER TABLE ONLY "public"."legacy_attver" ALTER COLUMN "Payload" SET (pg_attver=29111072);


--
-- Data for Name: legacy_attver; Type: TABLE DATA; Schema: public; Owner: -
--

COPY "public"."legacy_attver" ("id", "Payload") FROM stdin;
1	ready
2	copied
\.


--
-- Name: legacy_attver legacy_attver_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY "public"."legacy_attver"
    ADD CONSTRAINT "legacy_attver_pkey" PRIMARY KEY ("id");


--
-- PostgreSQL database dump complete
--

\unrestrict 4yt9rQJlfkkhceccLr8fgSW5yuNT8cfeLl0W52dA8k0k73y1mRdhisHjX4yC2oh

